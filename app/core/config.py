"""Application configuration module using Pydantic Settings with LRU caching."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Immutable application settings loaded from environment or defaults."""

    app_name: str = "FastAPI Clean Architecture"
    environment: str = "development"
    api_v1_prefix: str = "/api/v1"
    admin_api_key: str = "admin-secret-key-32chars-min-prod"
    user_api_key: str = "user-secret-key-32chars-min-prod"
    admin_username: str = "admin_user"
    default_username: str = "standard_user"
    debug: bool = False

    # Database Configuration (SQLAlchemy 2.0 Async)
    database_url: str = "sqlite+aiosqlite:///./app.db"
    db_echo: bool = False
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0
    db_pool_recycle: int = 1800
    db_pool_pre_ping: bool = True

    # Redis Configuration (redis.asyncio)
    redis_url: str = "redis://localhost:6379/0"
    redis_pool_size: int = 20
    redis_timeout: float = 2.0

    # RabbitMQ Configuration (aio-pika AMQP 0-9-1)
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    rabbitmq_pool_size: int = 10

    # JWT Cryptographic Configuration (HS256 / RS256)
    jwt_secret_key: str = "secret-key-for-jwt-signing-production-grade-32bytes"  # noqa: S105
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    jwt_private_key: str | None = None
    jwt_public_key: str | None = None

    # Field-Level Encryption (FLE) Configuration (Fernet AES-128-CBC + HMAC-SHA256)
    field_encryption_key: str = "c2R2Bycl-78k7z7RJp3DAn6_G2_3fpV8lYERo0B_AZo="

    # CORS Policy Configuration (Zero-Wildcard with Credentials)
    allowed_cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:8000",
        "https://myapp.com",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Cached singleton dependency provider for application settings.

    Guarantees O(1) retrieval after single-pass initialization across application lifetime.
    """
    return Settings()
