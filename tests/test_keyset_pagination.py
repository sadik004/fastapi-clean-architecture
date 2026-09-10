"""Comprehensive Test Suite for Day 67: Keyset / Cursor-Based Pagination Architecture.

Verifies:
1. Base64 Cursor Codec Test: Bidirectional serialization, exact timestamp & ID preservation,
   and strict InvalidCursorException enforcement on malformed/tampered tokens.
2. Sequential Keyset Page Traversal Test: 50 seeded records paginated with limit=10.
   Guarantees zero duplicate items and zero skipped items across all page boundaries.
3. Last Page Exhaustion Test: Correct detection of pagination terminus (has_more=False, next_cursor=None).
4. Deep Pagination B-Tree Seek Invariant: Deterministic composite tuple evaluation (created_at DESC, id DESC).
5. Offset Comparison Endpoint Test: Traditional SQL OFFSET query timing and data retrieval.
6. Boundary & Security Hardening: Tampered cursors return HTTP 400 with INVALID_CURSOR error code;
   out-of-bounds limits trigger HTTP 422.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import InvalidCursorException
from app.core.pagination.cursor import CursorCodec

# ==============================================================================
# 1. Base64 Cursor Codec Tests
# ==============================================================================


class TestCursorCodec:
    """Test suite for URL-safe Base64 opaque cursor token encoding and decoding."""

    def test_cursor_codec_roundtrip_preservation(self) -> None:
        """Verify that encoding and decoding preserves UTC datetime and ID with exact parity."""
        original_dt = datetime(2026, 9, 10, 12, 0, 0, 123456, tzinfo=UTC)
        original_id = 42

        encoded = CursorCodec.encode_cursor(original_dt, original_id)
        assert isinstance(encoded, str)
        assert len(encoded) > 0
        # Assert no padding chars in URL-safe output
        assert "=" not in encoded

        decoded_dt, decoded_id = CursorCodec.decode_cursor(encoded)
        assert decoded_dt == original_dt
        assert decoded_id == original_id

    def test_cursor_codec_handles_string_and_uuid_ids(self) -> None:
        """Verify cursor codec correctly serializes string and UUID-like IDs."""
        original_dt = datetime.now(UTC)
        str_id = "item_uuid_98765"

        encoded = CursorCodec.encode_cursor(original_dt, str_id)
        decoded_dt, decoded_id = CursorCodec.decode_cursor(encoded)

        assert decoded_dt == original_dt
        assert decoded_id == str_id

    @pytest.mark.parametrize(
        "malformed_cursor",
        [
            "",
            "   ",
            "not-base64!!@@##",
            base64.urlsafe_b64encode(b"not-json").decode("ascii"),
            base64.urlsafe_b64encode(b'["single_element"]').decode("ascii"),
            base64.urlsafe_b64encode(b'["2026-09-10T12:00:00Z", null]').decode("ascii"),
            base64.urlsafe_b64encode(b'[12345, "invalid_timestamp"]').decode("ascii"),
            base64.urlsafe_b64encode(b'["invalid-date-string", 1]').decode("ascii"),
            base64.urlsafe_b64encode(b'{"key": "value"}').decode("ascii"),
        ],
    )
    def test_cursor_codec_rejects_malformed_tokens(self, malformed_cursor: str) -> None:
        """Verify that malformed, corrupted, or tampered tokens raise InvalidCursorException."""
        with pytest.raises(InvalidCursorException):
            CursorCodec.decode_cursor(malformed_cursor)


@pytest.fixture
def seed_catalog_items(client: TestClient) -> list[dict[str, Any]]:
    """Seed exactly 50 distinct catalog items with strictly spaced timestamps."""
    created_items: list[dict[str, Any]] = []
    for i in range(1, 51):
        payload = {
            "sku": f"SKU-PAGE-{i:03d}",
            "name": f"Enterprise Test Item {i}",
            "category": "electronics" if i % 2 == 0 else "hardware",
            "price": float(i * 10.0),
            "barcode": f"BAR-PAGE-{i:04d}",
            "metadata_json": {"index": i, "batch": "test_50"},
            "tags": ["pagination", "keyset"],
        }
        res = client.post("/catalog/items", json=payload)
        assert res.status_code == 201, f"Seeding failed for item {i}: {res.text}"
        created_items.append(res.json())
    return created_items


# ==============================================================================
# 2. Sequential Keyset Page Traversal & Exhaustion Tests
# ==============================================================================


@pytest.mark.usefixtures("seed_catalog_items")
class TestKeysetPaginationEndpoints:
    """Integration test suite verifying API keyset cursor traversal against persisted database."""

    def test_sequential_keyset_page_traversal_zero_skips_zero_duplicates(
        self,
        client: TestClient,
    ) -> None:
        """Verify that traversing 50 items with limit=10 yields 0 duplicates and 0 skips."""
        page_size = 10
        total_items = 50
        seen_ids: list[int] = []
        cursor: str | None = None
        page_count = 0

        while True:
            url = f"/catalog/items/keyset?limit={page_size}"
            if cursor:
                url += f"&cursor={cursor}"

            response = client.get(url)
            assert response.status_code == 200, f"Failed at page {page_count + 1}: {response.text}"

            data = response.json()
            items = data["items"]
            has_more = data["has_more"]
            next_cursor = data["next_cursor"]
            total_returned = data["total_returned"]

            page_count += 1
            assert total_returned == len(items)

            # Collect IDs
            current_page_ids = [item["id"] for item in items]
            # Verify no duplicates within current page
            assert len(current_page_ids) == len(set(current_page_ids))

            # Verify no duplicates across all seen pages
            for item_id in current_page_ids:
                assert item_id not in seen_ids, f"Duplicate item ID {item_id} found on page {page_count}"
                seen_ids.append(item_id)

            if not has_more:
                assert next_cursor is None
                break

            assert next_cursor is not None
            cursor = next_cursor

        # Assert all 50 items were traversed across exactly 5 pages
        assert page_count == 5
        assert len(seen_ids) == total_items
        assert len(set(seen_ids)) == total_items

    def test_keyset_last_page_exhaustion(self, client: TestClient) -> None:
        """Verify terminus condition when final page boundary is reached."""
        # Request with limit=50 should return all items and has_more=False immediately
        response = client.get("/catalog/items/keyset?limit=50")
        assert response.status_code == 200
        data = response.json()

        assert data["total_returned"] == 50
        assert data["has_more"] is False
        assert data["next_cursor"] is None

    def test_keyset_single_item_pages_exhaustion(self, client: TestClient) -> None:
        """Verify keyset traversal with limit=1 across successive cursor calls."""
        cursor: str | None = None
        ids_collected: list[int] = []

        # Traverse 5 items one by one
        for _ in range(5):
            url = "/catalog/items/keyset?limit=1"
            if cursor:
                url += f"&cursor={cursor}"
            response = client.get(url)
            assert response.status_code == 200
            data = response.json()

            assert data["total_returned"] == 1
            assert data["has_more"] is True
            assert data["next_cursor"] is not None

            ids_collected.append(data["items"][0]["id"])
            cursor = data["next_cursor"]

        assert len(ids_collected) == 5
        assert len(set(ids_collected)) == 5

    def test_keyset_tampered_cursor_returns_http_400(self, client: TestClient) -> None:
        """Verify that providing a malformed/tampered cursor returns HTTP 400 INVALID_CURSOR."""
        tampered_cursor = "dGhpcy1pcy1ub3QtdmFsaWQtY3Vyc29y"
        response = client.get(f"/catalog/items/keyset?cursor={tampered_cursor}&limit=10")

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "INVALID_CURSOR"
        assert "Invalid, malformed, or tampered" in data["error"]["message"]

    def test_keyset_immune_to_concurrent_insert_drift(self, client: TestClient) -> None:
        """Verify that inserting new items during pagination does not cause duplicates or row shift."""
        # 1. Fetch Page 1 (items 1-10)
        res1 = client.get("/catalog/items/keyset?limit=10")
        assert res1.status_code == 200
        page1 = res1.json()
        page1_ids = [i["id"] for i in page1["items"]]
        cursor1 = page1["next_cursor"]
        assert cursor1 is not None

        # 2. Simulate concurrent insertion of 3 brand new items with latest timestamps
        for i in range(101, 104):
            new_res = client.post(
                "/catalog/items",
                json={
                    "sku": f"SKU-CONCURRENT-{i}",
                    "name": f"Concurrent Item {i}",
                    "category": "electronics",
                    "price": 99.0,
                    "barcode": f"BAR-CONCURRENT-{i}",
                    "metadata_json": {"concurrent": True},
                    "tags": ["concurrent"],
                },
            )
            assert new_res.status_code == 201

        # 3. Fetch Page 2 using cursor1 from Page 1
        res2 = client.get(f"/catalog/items/keyset?limit=10&cursor={cursor1}")
        assert res2.status_code == 200
        page2 = res2.json()
        page2_ids = [i["id"] for i in page2["items"]]

        # Assert zero overlap between Page 1 and Page 2 despite concurrent writes!
        assert set(page1_ids).isdisjoint(set(page2_ids))
        # Assert none of the newly inserted items polluted Page 2 (they are newer than cursor)
        assert all(i["sku"].startswith("SKU-PAGE-") for i in page2["items"])

    def test_deep_pagination_anchor_seek(self, client: TestClient) -> None:
        """Verify seeking directly using a cursor anchored at an arbitrary record."""
        # Fetch first page with limit=20
        res = client.get("/catalog/items/keyset?limit=20")
        assert res.status_code == 200
        first_page = res.json()
        target_item = first_page["items"][14]  # 15th item

        # Manually encode cursor for the 15th item
        target_dt = datetime.fromisoformat(target_item["created_at"])
        manual_cursor = CursorCodec.encode_cursor(target_dt, target_item["id"])

        # Fetch with manual cursor
        res_seek = client.get(f"/catalog/items/keyset?cursor={manual_cursor}&limit=5")
        assert res_seek.status_code == 200
        seek_data = res_seek.json()

        # The first item returned must be the 16th item (index 15 in original list)
        expected_next_id = first_page["items"][15]["id"]
        actual_first_id = seek_data["items"][0]["id"]
        assert actual_first_id == expected_next_id

    def test_keyset_limit_validation_constraints(self, client: TestClient) -> None:
        """Verify that limit < 1 or limit > 100 triggers HTTP 422 validation failure."""
        res_zero = client.get("/catalog/items/keyset?limit=0")
        assert res_zero.status_code == 422

        res_negative = client.get("/catalog/items/keyset?limit=-5")
        assert res_negative.status_code == 422

        res_over = client.get("/catalog/items/keyset?limit=101")
        assert res_over.status_code == 422



# ==============================================================================
# 3. Offset Comparison Endpoint Tests
# ==============================================================================


@pytest.mark.usefixtures("seed_catalog_items")
class TestOffsetComparisonEndpoint:
    """Test suite for the diagnostic traditional SQL OFFSET benchmarking route."""

    def test_offset_comparison_execution_and_telemetry(self, client: TestClient) -> None:
        """Verify GET /catalog/items/offset-comparison returns slice and execution latency."""
        # Query first page
        res1 = client.get("/catalog/items/offset-comparison?offset=0&limit=10")
        assert res1.status_code == 200
        data1 = res1.json()

        assert data1["limit"] == 10
        assert data1["offset"] == 0
        assert data1["total_returned"] == 10
        assert data1["scan_strategy"] == "OFFSET_SCAN_O_N"
        assert "execution_time_ms" in data1
        assert data1["execution_time_ms"] >= 0.0

        # Query next offset
        res2 = client.get("/catalog/items/offset-comparison?offset=10&limit=10")
        assert res2.status_code == 200
        data2 = res2.json()

        ids1 = {i["id"] for i in data1["items"]}
        ids2 = {i["id"] for i in data2["items"]}
        # Assert disjoint sets across pages
        assert ids1.isdisjoint(ids2)
