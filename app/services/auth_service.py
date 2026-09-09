"""Authentication Service implementing Stateless JWT & Redis Refresh Token Rotation (RTR)."""

import json
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.security import create_access_token, create_refresh_token, decode_jwt_token
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.user_service import UserService

logger = logging.getLogger(__name__)

RTR_FAMILY_PREFIX = "rtr:family:"


class AuthService:
    """Service orchestrating stateless token issuance and active refresh token rotation."""

    def __init__(
        self,
        user_service: UserService,
        redis: Redis,
        settings: Settings,
    ) -> None:
        self._user_service = user_service
        self._redis = redis
        self._settings = settings

    async def login(self, payload: LoginRequest) -> TokenResponse:
        """Authenticate user and issue a new token pair with a fresh rotation family in Redis."""
        user = await self._user_service.authenticate_user(
            username=payload.username,
            password=payload.password,
        )
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive",
            )

        role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
        family_id = uuid.uuid4().hex

        access_token = create_access_token(user_id=user.id, role=role_str)
        refresh_token, jti = create_refresh_token(user_id=user.id, family_id=family_id)

        # Initialize rotation family state in Redis with explicit TTL
        family_key = f"{RTR_FAMILY_PREFIX}{family_id}"
        state: dict[str, Any] = {
            "active_jti": jti,
            "user_id": user.id,
            "role": role_str,
        }
        ttl_seconds = self._settings.jwt_refresh_token_expire_days * 86400
        await self._redis.set(family_key, json.dumps(state), ex=ttl_seconds)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=self._settings.jwt_access_token_expire_minutes * 60,
        )

    async def refresh_tokens(self, refresh_token: str) -> TokenResponse:
        """Rotate refresh token: burns old token, checks theft/reuse, issues new token pair.

        Guarantees:
        - If presented jti matches active_jti: Legitimate rotation -> updates active_jti.
        - If presented jti != active_jti: Theft/replay attack detected -> immediately revokes
          entire token family in Redis and rejects request with HTTP 401.
        """
        payload = decode_jwt_token(refresh_token, expected_type="refresh")
        family_id = str(payload.get("family_id", ""))
        incoming_jti = str(payload.get("jti", ""))
        user_id = int(payload.get("sub", 0))

        if not family_id or not incoming_jti:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Malformed refresh token claims",
                headers={"WWW-Authenticate": "Bearer"},
            )

        family_key = f"{RTR_FAMILY_PREFIX}{family_id}"
        raw_state = await self._redis.get(family_key)
        if raw_state is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token expired or revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        state: dict[str, Any] = json.loads(raw_state)
        active_jti = str(state.get("active_jti", ""))

        # Theft / Replay Attack Detection
        if incoming_jti != active_jti:
            # Compromised token chain detected! A previously burned token was reused.
            await self._redis.delete(family_key)
            logger.warning(
                "SECURITY ALERT: Refresh token reuse detected on family '%s'. "
                "Revoking all sessions for user %s.",
                family_id,
                user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token reuse detected. All sessions revoked for security.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Legitimate rotation: burn old token, issue new token pair
        new_refresh_token, new_jti = create_refresh_token(user_id=user_id, family_id=family_id)
        role_str = str(state.get("role", "user"))
        new_access_token = create_access_token(user_id=user_id, role=role_str)

        # Update active_jti in Redis while preserving remaining family TTL
        remaining_ttl = await self._redis.ttl(family_key)
        if remaining_ttl <= 0:
            remaining_ttl = self._settings.jwt_refresh_token_expire_days * 86400

        state["active_jti"] = new_jti
        await self._redis.set(family_key, json.dumps(state), ex=remaining_ttl)

        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            expires_in=self._settings.jwt_access_token_expire_minutes * 60,
        )

    async def logout(self, refresh_token: str) -> None:
        """Revoke the refresh token family in Redis, invalidating all sessions in this chain."""
        try:
            payload = decode_jwt_token(refresh_token, expected_type="refresh")
            family_id = payload.get("family_id")
            if family_id:
                await self._redis.delete(f"{RTR_FAMILY_PREFIX}{family_id}")
        except HTTPException:
            # If the token is already expired or malformed, revocation is a no-op
            pass
