"""Integration test suite verifying full User CRUD lifecycle and FastAPI Dependency Injection."""

from typing import Generator
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.user_router import get_user_repository


@pytest.fixture(autouse=True)
def clean_repository() -> Generator[None, None, None]:
    """Fixture to reset repository state between tests."""
    repo = get_user_repository()
    repo.clear()
    yield
    repo.clear()


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture."""
    return TestClient(app)


def test_complete_crud_lifecycle(client: TestClient) -> None:
    """Verify complete CRUD lifecycle: POST -> GET -> PUT -> DELETE -> GET."""
    # 1. CREATE (POST)
    create_payload = {
        "email": "lifecycle@example.com",
        "username": "lifecycle_user",
        "password": "SecurePassword123!",
        "password_confirm": "SecurePassword123!",
        "age": 22,
        "role": "user",
    }
    create_resp = client.post("/users/", json=create_payload)
    assert create_resp.status_code == 201
    user_data = create_resp.json()
    user_id = user_data["id"]
    assert user_data["email"] == "lifecycle@example.com"
    assert user_data["username"] == "lifecycle_user"
    assert user_data["age"] == 22
    assert user_data["role"] == "user"

    # 2. READ (GET)
    get_resp = client.get(f"/users/{user_id}")
    assert get_resp.status_code == 200
    assert get_resp.json() == user_data

    # 3. UPDATE (PUT)
    update_payload = {
        "email": "updated_email@example.com",
        "username": "updated_handle",
        "age": 25,
        "role": "admin",
    }
    update_resp = client.put(f"/users/{user_id}", json=update_payload)
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert updated_data["email"] == "updated_email@example.com"
    assert updated_data["username"] == "updated_handle"
    assert updated_data["age"] == 25
    assert updated_data["role"] == "admin"

    # 4. DELETE (DELETE)
    delete_resp = client.delete(f"/users/{user_id}")
    assert delete_resp.status_code == 204
    assert delete_resp.text == ""

    # 5. READ AFTER DELETE (GET) -> 404
    get_after_delete = client.get(f"/users/{user_id}")
    assert get_after_delete.status_code == 404

    # 6. RE-REGISTRATION -> 201 (Index purging verification)
    recreate_resp = client.post(
        "/users/",
        json={
            "email": "updated_email@example.com",
            "username": "updated_handle",
            "password": "AnotherPassword123!",
            "password_confirm": "AnotherPassword123!",
        },
    )
    assert recreate_resp.status_code == 201
    assert recreate_resp.json()["email"] == "updated_email@example.com"


def test_put_user_conflict_scenarios(client: TestClient) -> None:
    """Verify PUT returns 409 Conflict when updating to an existing email or username."""
    client.post(
        "/users/",
        json={
            "email": "user1@example.com",
            "username": "handle_one",
            "password": "Password123!",
            "password_confirm": "Password123!",
        },
    )

    user2 = client.post(
        "/users/",
        json={
            "email": "user2@example.com",
            "username": "handle_two",
            "password": "Password123!",
            "password_confirm": "Password123!",
        },
    ).json()

    # Attempt to take user1's email
    conflict_email_resp = client.put(
        f"/users/{user2['id']}",
        json={"email": "user1@example.com"},
    )
    assert conflict_email_resp.status_code == 409
    assert "already registered" in conflict_email_resp.json()["detail"]

    # Attempt to take user1's username
    conflict_username_resp = client.put(
        f"/users/{user2['id']}",
        json={"username": "handle_one"},
    )
    assert conflict_username_resp.status_code == 409
    assert "already taken" in conflict_username_resp.json()["detail"]


def test_put_user_not_found(client: TestClient) -> None:
    """Verify PUT on non-existent ID returns 404 Not Found."""
    response = client.put("/users/9999", json={"username": "does_not_exist"})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_delete_user_not_found(client: TestClient) -> None:
    """Verify DELETE on non-existent ID returns 404 Not Found."""
    response = client.delete("/users/9999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_list_users_pagination(client: TestClient) -> None:
    """Verify GET /users/ supports limit and offset pagination parameters."""
    for i in range(5):
        client.post(
            "/users/",
            json={
                "email": f"page_user_{i}@example.com",
                "username": f"page_user_{i}",
                "password": "Password123!",
                "password_confirm": "Password123!",
            },
        )

    # Fetch page 1 (limit=2, offset=0)
    resp_page1 = client.get("/users/?limit=2&offset=0")
    assert resp_page1.status_code == 200
    data_page1 = resp_page1.json()
    assert len(data_page1) == 2
    assert [u["username"] for u in data_page1] == ["page_user_0", "page_user_1"]

    # Fetch page 2 (limit=2, offset=2)
    resp_page2 = client.get("/users/?limit=2&offset=2")
    assert resp_page2.status_code == 200
    data_page2 = resp_page2.json()
    assert len(data_page2) == 2
    assert [u["username"] for u in data_page2] == ["page_user_2", "page_user_3"]

    # Invalid pagination bounds
    assert client.get("/users/?limit=0").status_code == 422
    assert client.get("/users/?limit=101").status_code == 422
    assert client.get("/users/?offset=-1").status_code == 422
