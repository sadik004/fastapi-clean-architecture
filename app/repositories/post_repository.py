"""Post Repository & Entity definitions preventing N+1 queries via joinedload eager loading."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.post import PostModel
from app.repositories.user_repository import UserEntity


@dataclass
class PostEntity:
    """Domain entity representing an authored post, detached from ORM sessions."""

    id: int
    title: str
    content: str
    user_id: int
    created_at: datetime
    author: Optional[UserEntity] = None


class PostRepositoryProtocol(Protocol):
    """Abstract protocol for asynchronous post persistence operations."""

    async def create(self, title: str, content: str, user_id: int) -> PostEntity:
        """Create and persist a new post."""
        ...

    async def get_by_id(self, post_id: int) -> Optional[PostEntity]:
        """Fetch post by ID without author relationship."""
        ...

    async def get_post_with_author(self, post_id: int) -> Optional[PostEntity]:
        """Fetch post with eagerly loaded author via joinedload (strictly 1 SQL query)."""
        ...


class SqlAlchemyPostRepository:
    """Production asynchronous repository for Post persistence backed by SQLAlchemy 2.0.

    Demonstrates:
    - Many-to-One scalar eager loading using joinedload() (LEFT OUTER JOIN in 1 single query).
    - Zero ORM Leakage Boundary: maps PostModel and UserModel to PostEntity and UserEntity.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    @staticmethod
    def _to_entity(model: PostModel, include_author: bool = False) -> PostEntity:
        """Map PostModel to pure detached domain PostEntity."""
        created_at = model.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        author_entity: Optional[UserEntity] = None
        if include_author and getattr(model, "author", None) is not None:
            from app.repositories.sqlalchemy_user_repository import (
                SqlAlchemyUserRepository,
            )

            author_entity = SqlAlchemyUserRepository._to_entity(model.author)

        return PostEntity(
            id=model.id,
            title=model.title,
            content=model.content,
            user_id=model.user_id,
            created_at=created_at,
            author=author_entity,
        )

    async def create(self, title: str, content: str, user_id: int) -> PostEntity:
        """Create and persist a new post in the database."""
        model = PostModel(
            title=title,
            content=content,
            user_id=user_id,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_id(self, post_id: int) -> Optional[PostEntity]:
        """Fetch post by primary key ID without author."""
        stmt = select(PostModel).where(PostModel.id == post_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model is not None else None

    async def get_post_with_author(self, post_id: int) -> Optional[PostEntity]:
        """Fetch post with author using joinedload() (strictly 1 SQL query).

        LEFT OUTER JOIN eliminates round-trip latency and guarantees scalar parent
        is fetched in a single database round-trip without Cartesian product overhead.
        """
        stmt = (
            select(PostModel)
            .options(joinedload(PostModel.author))
            .where(PostModel.id == post_id)
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model, include_author=True) if model is not None else None


class InMemoryPostRepository:
    """In-memory implementation of PostRepositoryProtocol for fast, isolated testing."""

    def __init__(self) -> None:
        self._store: dict[int, PostEntity] = {}
        self._current_id: int = 0

    async def create(self, title: str, content: str, user_id: int) -> PostEntity:
        """Create and persist a new post entity in memory in O(1) time."""
        self._current_id += 1
        entity = PostEntity(
            id=self._current_id,
            title=title,
            content=content,
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
            author=None,
        )
        self._store[self._current_id] = entity
        return entity

    async def get_by_id(self, post_id: int) -> Optional[PostEntity]:
        """Fetch post by primary key ID in O(1) time."""
        return self._store.get(post_id)

    async def get_post_with_author(self, post_id: int) -> Optional[PostEntity]:
        """Fetch post by ID in O(1) time."""
        return self._store.get(post_id)

    def clear(self) -> None:
        """Reset internal in-memory post storage."""
        self._store.clear()
        self._current_id = 0


__all__ = [
    "InMemoryPostRepository",
    "PostEntity",
    "PostRepositoryProtocol",
    "SqlAlchemyPostRepository",
]
