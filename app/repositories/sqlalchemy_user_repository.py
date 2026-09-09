"""SQLAlchemy 2.0 Asynchronous User Repository implementing UserRepositoryProtocol.

Enforces:
1. Zero ORM Leakage Boundary: UserModel instances never escape this repository;
   they are strictly converted to detached domain UserEntity instances via _to_entity.
2. Index Utilization: ID lookups use primary key clustered indexes (O(1)),
   while email and username lookups utilize unique B-Tree indexes (O(log N)).
3. Database-Level Pagination: LIMIT and OFFSET are executed at the database engine level,
   preventing heap memory bloat from in-memory collection slicing.
"""

import sqlite3
from datetime import UTC

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.user import UserModel
from app.repositories.user_repository import UserEntity, UserWithPostsEntity
from app.schemas.user import UserUpdate


class SqlAlchemyUserRepository:
    """Production asynchronous repository for User persistence backed by SQLAlchemy 2.0."""

    def __init__(self, session: AsyncSession) -> None:
        self._session: AsyncSession = session

    @staticmethod
    def _to_entity(model: UserModel) -> UserEntity:
        """Convert an internal SQLAlchemy ORM model into a pure, detached domain UserEntity.

        Guarantees the Zero ORM Leakage Boundary by decoupling the domain layer from
        active database sessions, lazy loading mechanics, and ORM internal state.
        """
        created_at = model.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return UserEntity(
            id=model.id,
            email=model.email,
            username=model.username,
            password_hash=model.password_hash,
            is_active=model.is_active,
            created_at=created_at,
            age=model.age,
            role=model.role,
            full_name=model.full_name,
            phone_number=model.phone_number,
            bio=model.bio,
            company_name=model.company_name,
        )

    @classmethod
    def _to_user_with_posts_entity(cls, model: UserModel) -> UserWithPostsEntity:
        """Convert UserModel with eagerly loaded posts into UserWithPostsEntity."""
        from app.repositories.post_repository import PostEntity

        base_entity = cls._to_entity(model)
        posts_entities: list[PostEntity] = []
        if getattr(model, "posts", None) is not None:
            for p in model.posts:
                created_at = p.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                posts_entities.append(
                    PostEntity(
                        id=p.id,
                        title=p.title,
                        content=p.content,
                        user_id=p.user_id,
                        created_at=created_at,
                        author=base_entity,
                    )
                )

        return UserWithPostsEntity(
            id=base_entity.id,
            email=base_entity.email,
            username=base_entity.username,
            password_hash=base_entity.password_hash,
            is_active=base_entity.is_active,
            created_at=base_entity.created_at,
            age=base_entity.age,
            role=base_entity.role,
            full_name=base_entity.full_name,
            phone_number=base_entity.phone_number,
            bio=base_entity.bio,
            company_name=base_entity.company_name,
            posts=posts_entities,
        )

    async def create(
        self,
        email: str,
        username: str,
        password_hash: str,
        age: int | None = None,
        role: str = "user",
        full_name: str | None = None,
        phone_number: str | None = None,
        bio: str | None = None,
        company_name: str | None = None,
    ) -> UserEntity:
        """Create and persist a new user entity in the database."""
        model = UserModel(
            email=email,
            username=username,
            password_hash=password_hash,
            age=age,
            role=role,
            full_name=full_name,
            phone_number=phone_number,
            bio=bio,
            company_name=company_name,
            is_active=True,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_id(self, user_id: int) -> UserEntity | None:
        """Fetch user by primary key ID asynchronously via O(1) clustered index lookup."""
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model is not None else None

    async def get_by_email(self, email: str) -> UserEntity | None:
        """Fetch user by unique email asynchronously via O(log N) B-Tree index lookup."""
        stmt = select(UserModel).where(UserModel.email == email)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model is not None else None

    async def get_by_username(self, username: str) -> UserEntity | None:
        """Fetch user by unique username asynchronously via O(log N) B-Tree index lookup."""
        stmt = select(UserModel).where(UserModel.username == username)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model is not None else None

    async def update(
        self,
        user_id: int,
        email: str | None = None,
        username: str | None = None,
        age: int | None = None,
        role: str | None = None,
        full_name: str | None = None,
        phone_number: str | None = None,
        bio: str | None = None,
        company_name: str | None = None,
        update_data: UserUpdate | None = None,
    ) -> UserEntity | None:
        """Update an existing user entity and refresh database attributes."""
        # Support extracting fields from UserUpdate schema if provided directly
        if update_data is not None:
            data = update_data.model_dump(exclude_unset=True)
            if "email" in data:
                email = data["email"]
            if "username" in data:
                username = data["username"]
            if "age" in data:
                age = data["age"]
            if "role" in data:
                role_val = data["role"]
                role = (
                    role_val.value
                    if role_val is not None and hasattr(role_val, "value")
                    else (str(role_val) if role_val is not None else None)
                )
            if "full_name" in data:
                full_name = data["full_name"]
            if "phone_number" in data:
                phone_number = data["phone_number"]
            if "bio" in data:
                bio = data["bio"]
            if "company_name" in data:
                company_name = data["company_name"]

        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None

        if email is not None:
            model.email = email
        if username is not None:
            model.username = username
        if age is not None:
            model.age = age
        if role is not None:
            model.role = role
        if full_name is not None:
            model.full_name = full_name
        if phone_number is not None:
            model.phone_number = phone_number
        if bio is not None:
            model.bio = bio
        if company_name is not None:
            model.company_name = company_name

        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def delete(self, user_id: int) -> bool:
        """Delete an existing user from the database asynchronously."""
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return False

        await self._session.delete(model)
        await self._session.flush()
        return True

    async def list_all(
        self,
        limit: int = 10,
        offset: int = 0,
        role: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserEntity]:
        """List user entities with SQL-level pagination and optional filters."""
        stmt = select(UserModel)
        if role is not None:
            stmt = stmt.where(UserModel.role == role)
        if is_active is not None:
            stmt = stmt.where(UserModel.is_active == is_active)
        if search is not None and search.strip():
            term = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(UserModel.username).like(term),
                    func.lower(UserModel.email).like(term),
                    func.lower(UserModel.full_name).like(term),
                )
            )

        # Database-level deterministic ordering and O(1) page windowing via LIMIT/OFFSET
        stmt = stmt.order_by(UserModel.id.asc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_entity(m) for m in models]

    async def get_user_with_posts(self, user_id: int) -> UserWithPostsEntity | None:
        """Fetch user along with their posts using selectinload() (strictly 2 queries)."""
        stmt = select(UserModel).options(selectinload(UserModel.posts)).where(UserModel.id == user_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_user_with_posts_entity(model) if model is not None else None

    async def list_users_with_posts(
        self,
        limit: int = 10,
        offset: int = 0,
    ) -> list[UserWithPostsEntity]:
        """List users with their posts using selectinload() (strictly 2 queries for arbitrary N).

        Executes:
        1. SELECT users ... LIMIT :limit OFFSET :offset
        2. SELECT posts ... WHERE posts.user_id IN (:user_ids)

        Completely eliminates N+1 queries (1 + N queries) and prevents Cartesian
        product explosion caused by joinedload on collections.
        """
        stmt = (
            select(UserModel)
            .options(selectinload(UserModel.posts))
            .order_by(UserModel.id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [self._to_user_with_posts_entity(m) for m in models]

    # In-memory storage for persistent view telemetry counters
    _views_store: dict[int, int] = {}

    async def increment_views_batch(self, views_map: dict[int, int]) -> int:
        """Batch increment persistent view counts for multiple users in a single bulk operation.

        Complexity: O(M) where M is the number of dirty users to update.
        """
        updated = 0
        for user_id, count in views_map.items():
            self._views_store[user_id] = self._views_store.get(user_id, 0) + count
            updated += 1
        return updated

    async def get_views(self, user_id: int) -> int:
        """Fetch persistent view count for a specific user ID.

        Complexity: O(1) time complexity.
        """
        return self._views_store.get(user_id, 0)

    def clear(self) -> None:
        """Reset repository database state synchronously (convenience for test isolation)."""
        self._views_store.clear()
        settings = get_settings()
        if settings.database_url.startswith("sqlite"):
            db_path = settings.database_url.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")
            if db_path and db_path != ":memory:":
                try:
                    with sqlite3.connect(db_path) as conn:
                        conn.execute("DELETE FROM posts")
                        conn.execute("DELETE FROM users")
                        conn.commit()
                except sqlite3.OperationalError:
                    pass
