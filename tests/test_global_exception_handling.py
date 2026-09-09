"""Comprehensive test suite for Day 14: Global Exception Handling, Error Envelope & 500 Masking."""

from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

from app.core.exceptions import (
    AuthorizationException,
    BaseDomainException,
    BusinessRuleViolationException,
    DomainException,
    EntityConflictException,
    EntityNotFoundException,
    UserAlreadyExistsException,
    UserNotFoundException,
)
from app.main import app
from app.schemas.error import ErrorResponse


class TestGlobalExceptionHandling:
    """Verification suite for centralized error responses, domain hierarchy, and safe masking."""

    def test_404_entity_not_found_unified_envelope(self, client: TestClient) -> None:
        """Verify looking up non-existent user returns unified ErrorResponse with USER_NOT_FOUND."""
        response = client.get("/users/999999")
        assert response.status_code == 404

        data = response.json()
        assert "error" in data
        error = data["error"]

        # Validate structured fields — UserNotFoundException uses domain-specific code USER_NOT_FOUND
        assert error["code"] == "USER_NOT_FOUND"
        assert error["status_code"] == 404
        assert "999999" in error["message"]
        assert "not found" in error["message"].lower()
        assert isinstance(error["trace_id"], str) and len(error["trace_id"]) > 0
        assert isinstance(datetime.fromisoformat(error["timestamp"]), datetime)
        assert error["details"] is None

        # Validate schema compliance
        validated = ErrorResponse.model_validate(data)
        assert validated.error.code == "USER_NOT_FOUND"

    def test_404_by_username_unified_envelope(self, client: TestClient) -> None:
        """Verify non-existent username lookup returns USER_NOT_FOUND envelope."""
        response = client.get("/users/by-username/unknown_ghost")
        assert response.status_code == 404

        data = response.json()
        assert data["error"]["code"] == "USER_NOT_FOUND"
        assert data["error"]["status_code"] == 404
        assert "unknown_ghost" in data["error"]["message"]

    def test_409_entity_conflict_unified_envelope(
        self,
        client: TestClient,
        sample_user_payload: dict[str, Any],
    ) -> None:
        """Verify duplicate user creation returns unified ErrorResponse with ENTITY_CONFLICT."""
        # Initial successful registration
        first_resp = client.post("/users/", json=sample_user_payload)
        assert first_resp.status_code == 201

        # Duplicate registration attempt
        conflict_resp = client.post("/users/", json=sample_user_payload)
        assert conflict_resp.status_code == 409

        data = conflict_resp.json()
        assert "error" in data
        error = data["error"]

        assert error["code"] == "ENTITY_CONFLICT"
        assert error["status_code"] == 409
        assert "already exists" in error["message"].lower() or "already registered" in error["message"].lower()
        assert isinstance(error["trace_id"], str) and len(error["trace_id"]) > 0
        assert error["details"] is None

        # Validate schema compliance
        validated = ErrorResponse.model_validate(data)
        assert validated.error.code == "ENTITY_CONFLICT"

    def test_422_field_validation_unified_envelope(self, client: TestClient) -> None:
        """Verify invalid payload returns unified ErrorResponse with structured field details."""
        invalid_payload = {
            "email": "not-an-email",
            "username": "ab",  # min length is 3
            "password": "short",
            "age": 15,  # min age is 18
            "role": "invalid_role",
        }

        response = client.post("/users/", json=invalid_payload)
        assert response.status_code == 422

        data = response.json()
        assert "error" in data
        error = data["error"]

        assert error["code"] == "FIELD_VALIDATION_ERROR"
        assert error["status_code"] == 422
        assert "validation failed" in error["message"].lower()
        assert isinstance(error["details"], list) and len(error["details"]) > 0
        assert isinstance(error["trace_id"], str) and len(error["trace_id"]) > 0

        # Verify structured error details contain field pointers
        fields_with_errors = [d.get("field") for d in error["details"]]
        assert any("email" in f for f in fields_with_errors if f)
        assert any("username" in f for f in fields_with_errors if f)

        # Validate schema compliance
        validated = ErrorResponse.model_validate(data)
        assert validated.error.code == "FIELD_VALIDATION_ERROR"

    def test_500_unhandled_exception_masked_and_trace_id(self) -> None:
        """Verify unhandled 500 error returns masked ErrorResponse with zero traceback leakage."""
        client_no_raise = TestClient(app, raise_server_exceptions=False)
        response = client_no_raise.get("/test/raise-unhandled-500")

        assert response.status_code == 500
        data = response.json()
        assert "error" in data
        error = data["error"]

        # Code and sanitized message verification
        assert error["code"] == "INTERNAL_SERVER_ERROR"
        assert error["status_code"] == 500
        assert error["message"] == "An unexpected error occurred. Please contact support with trace ID."
        assert isinstance(error["trace_id"], str) and len(error["trace_id"]) > 0

        # Strict Security Rule: Zero information leakage
        raw_text = response.text
        assert "SuperSecret" not in raw_text
        assert "Traceback" not in raw_text
        assert "RuntimeError" not in raw_text
        assert "database crash" not in raw_text

        # Validate schema compliance
        validated = ErrorResponse.model_validate(data)
        assert validated.error.code == "INTERNAL_SERVER_ERROR"

    def test_401_and_403_security_exceptions_preserve_headers(
        self,
        client: TestClient,
        standard_user: dict[str, Any],
        auth_headers: dict[str, str],
    ) -> None:
        """Verify authentication & authorization errors return ErrorResponse and preserve headers."""
        # 401 Unauthorized without header
        unauth_resp = client.get("/users/me")
        assert unauth_resp.status_code == 401
        assert unauth_resp.headers.get("WWW-Authenticate") == "ApiKey"

        unauth_data = unauth_resp.json()
        assert unauth_data["error"]["code"] == "UNAUTHORIZED"
        assert unauth_data["error"]["status_code"] == 401
        assert "missing api key" in unauth_data["error"]["message"].lower()

        # 403 Forbidden with standard user trying to access admin endpoint
        forbidden_resp = client.get("/users/admin/metrics", headers=auth_headers)
        assert forbidden_resp.status_code == 403

        forbidden_data = forbidden_resp.json()
        assert forbidden_data["error"]["code"] == "FORBIDDEN"
        assert forbidden_data["error"]["status_code"] == 403
        assert "insufficient role permissions" in forbidden_data["error"]["message"].lower()

    def test_domain_exception_hierarchy_invariants(self) -> None:
        """Verify decoupled domain exception hierarchy inheritance and default codes."""
        # Root inheritance
        assert issubclass(DomainException, BaseDomainException)
        assert issubclass(EntityNotFoundException, BaseDomainException)
        assert issubclass(EntityConflictException, BaseDomainException)
        assert issubclass(AuthorizationException, BaseDomainException)
        assert issubclass(BusinessRuleViolationException, BaseDomainException)
        assert issubclass(UserNotFoundException, EntityNotFoundException)
        assert issubclass(UserAlreadyExistsException, EntityConflictException)

        # Default codes — UserNotFoundException now uses domain-specific USER_NOT_FOUND (Day 49)
        assert EntityNotFoundException().code == "ENTITY_NOT_FOUND"
        assert EntityConflictException().code == "ENTITY_CONFLICT"
        assert AuthorizationException().code == "AUTHORIZATION_FAILED"
        assert BusinessRuleViolationException().code == "BUSINESS_RULE_VIOLATION"
        assert UserNotFoundException(123).code == "USER_NOT_FOUND"  # upgraded Day 49: ENTITY_NOT_FOUND → USER_NOT_FOUND
        assert UserAlreadyExistsException().code == "ENTITY_CONFLICT"
