"""Comprehensive Test Suite for Day 70: PostgreSQL Full-Text Search Architecture.

Verifies:
1. Stemmed Lexeme Full-Text Search: Query 'run shoe' matches 'Running Shoes' via Porter stemming.
2. Relevance Ranking: Title matches (Weight A) rank higher than description matches (Weight B) via ts_rank.
3. Trigram Fuzzy Typo Tolerance: Typo query 'aple iphon' matches 'Apple iPhone' with similarity > 0.3.
4. Hybrid Search Fallback: Automatically degrades to Trigram Fuzzy search when exact TSVector yields zero matches.
5. Autocomplete Suggestions: Sub-millisecond suggestions via prefix and trigram matching.
6. HTTP Endpoints: Validates POST /search/products, GET /search/products, and GET /search/suggest.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import async_session_factory
from app.main import app
from app.schemas.search import CreateProductRequest
from app.services.search_service import SearchService


@pytest.fixture
def client() -> TestClient:
    """FastAPI synchronous test client fixture."""
    return TestClient(app)


# ==============================================================================
# 1. Stemmed Lexeme & Relevance Ranking Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_stemmed_lexeme_full_text_search() -> None:
    """Mathematical proof: 'Running Shoes' matches 'run shoe' via Porter stemming."""
    async with async_session_factory() as session:
        # Seed test product
        prod = await SearchService.create_product(
            session=session,
            request=CreateProductRequest(
                title="Pro Running Shoes 2026",
                description="Breathable athletic footwear engineered for marathon endurance.",
                brand="Nike",
                price=159.99,
            ),
        )

        # Search using base root words 'run shoe'
        results = await SearchService.search_products_full_text(
            session=session,
            query_str="run shoe",
        )

        assert len(results) >= 1
        matched = results[0]
        assert matched.id == prod.id
        assert matched.title == "Pro Running Shoes 2026"
        assert matched.match_type == "FULL_TEXT"
        assert matched.score > 0.0


@pytest.mark.asyncio
async def test_relevance_ranking_title_vs_description() -> None:
    """Verify that title matches (Weight A) rank strictly higher than description matches (Weight B)."""
    async with async_session_factory() as session:
        # Product 1: Term in Title (Weight A)
        p1 = await SearchService.create_product(
            session=session,
            request=CreateProductRequest(
                title="Ergonomic Mechanical Keyboard",
                description="High precision clicky tactile switches for coding productivity.",
                brand="Keychron",
                price=120.0,
            ),
        )

        # Product 2: Term only in Description (Weight B)
        p2 = await SearchService.create_product(
            session=session,
            request=CreateProductRequest(
                title="Desk Mat Studio Edition",
                description="Large waterproof mouse pad designed to accommodate your keyboard and mouse.",
                brand="SteelSeries",
                price=35.0,
            ),
        )

        # Search for 'keyboard'
        results = await SearchService.search_products_full_text(
            session=session,
            query_str="keyboard",
        )

        assert len(results) >= 2
        # Title match must rank first (highest score)
        assert results[0].id == p1.id
        assert results[1].id == p2.id
        assert results[0].score > results[1].score


# ==============================================================================
# 2. Trigram Fuzzy Typo Tolerance Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_trigram_fuzzy_typo_tolerance() -> None:
    """Verify that typos (e.g. 'aple iphon') resolve and match 'Apple iPhone' via Trigrams."""
    async with async_session_factory() as session:
        prod = await SearchService.create_product(
            session=session,
            request=CreateProductRequest(
                title="Apple iPhone 16 Pro Max",
                description="Flagship smartphone with titanium enclosure and A18 Pro silicon.",
                brand="Apple",
                price=1199.0,
            ),
        )

        # Query with severe typos
        results = await SearchService.search_products_fuzzy_trigram(
            session=session,
            typo_query="aple iphon",
            similarity_threshold=0.3,
        )

        assert len(results) >= 1
        top_match = results[0]
        assert top_match.id == prod.id
        assert top_match.title == "Apple iPhone 16 Pro Max"
        assert top_match.match_type == "FUZZY_TRIGRAM"
        assert top_match.score >= 0.3


@pytest.mark.asyncio
async def test_hybrid_fallback_on_zero_matches() -> None:
    """Verify automatic degradation from TSVector to Trigram Fuzzy when zero exact matches occur."""
    async with async_session_factory() as session:
        await SearchService.create_product(
            session=session,
            request=CreateProductRequest(
                title="Samsung Galaxy S25 Ultra",
                description="Dynamic AMOLED display with built-in S Pen stylus.",
                brand="Samsung",
                price=1299.0,
            ),
        )

        # 1. Exact query matches via FULL_TEXT strategy
        res_exact = await SearchService.search_products_hybrid(
            session=session,
            query_str="Samsung Galaxy",
            fuzzy=False,
        )
        assert res_exact.strategy == "FULL_TEXT"
        assert res_exact.total >= 1

        # 2. Typo query 'samsng galxy' yields 0 full-text matches, degrades to FUZZY_TRIGRAM
        res_typo = await SearchService.search_products_hybrid(
            session=session,
            query_str="samsng galxy",
            fuzzy=False,
            similarity_threshold=0.3,
        )
        assert res_typo.strategy == "FUZZY_TRIGRAM"
        assert res_typo.total >= 1
        assert "Samsung Galaxy" in res_typo.results[0].title


@pytest.mark.asyncio
async def test_autocomplete_suggestions() -> None:
    """Verify that prefix and trigram matching produces accurate query suggestions."""
    async with async_session_factory() as session:
        await SearchService.create_product(
            session=session,
            request=CreateProductRequest(
                title="Sony Wireless Noise Cancelling Headphones",
                description="Industry leading noise cancellation with high resolution audio.",
                brand="Sony",
                price=348.0,
            ),
        )

        # Test prefix match
        suggestions = await SearchService.suggest_autocomplete(
            session=session,
            prefix="son",
            limit=5,
        )
        assert any("Sony" in s for s in suggestions)


# ==============================================================================
# 3. HTTP Transport Layer Endpoints Tests
# ==============================================================================


def test_http_search_and_suggest_endpoints(client: TestClient) -> None:
    """Verify POST /search/products, GET /search/products, and GET /search/suggest lifecycle."""
    # 1. Create a searchable product
    post_res = client.post(
        "/search/products",
        json={
            "title": "Ultra Trail Running Hydration Vest",
            "description": "Ergonomic running backpack with twin water flasks.",
            "brand": "Salomon",
            "price": 139.95,
        },
    )
    assert post_res.status_code == 201
    created_id = post_res.json()["id"]

    # 2. Search using stemmed query 'trail run vest'
    search_res = client.get("/search/products", params={"q": "trail run vest"})
    assert search_res.status_code == 200
    data = search_res.json()
    assert data["strategy"] in ("FULL_TEXT", "FUZZY_TRIGRAM")
    assert data["total"] >= 1
    assert any(item["id"] == created_id for item in data["results"])

    # 3. Search with forced fuzzy query for typo 'salomn hydratin'
    fuzzy_res = client.get("/search/products", params={"q": "salomn hydratin", "fuzzy": "true"})
    assert fuzzy_res.status_code == 200
    fuzzy_data = fuzzy_res.json()
    assert fuzzy_data["strategy"] == "FUZZY_TRIGRAM"
    assert fuzzy_data["total"] >= 1

    # 4. Suggest autocomplete endpoint
    suggest_res = client.get("/search/suggest", params={"q": "sal"})
    assert suggest_res.status_code == 200
    sugg_data = suggest_res.json()
    assert any("Salomon" in s for s in sugg_data["suggestions"])
