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
