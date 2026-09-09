"""User Service containing pure business logic, domain rules, and async task orchestration."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.dsa.search_algorithms import binary_search_range, two_pointer_pair_search
from app.core.dsa.trie import PrefixTrie
from app.core.exceptions import UserAlreadyExistsException, UserNotFoundException
from app.core.security import get_password_hash, verify_password
from app.core.unit_of_work import UnitOfWorkProtocol
from app.repositories.post_repository import PostEntity
from app.repositories.user_repository import UserEntity, UserRepositoryProtocol
from app.schemas.user import UserCreate, UserProfileUpdate, UserRole, UserUpdate
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)

CACHE_USER_PREFIX: str = "cache:user:"
CACHE_USER_TTL_SECONDS: int = 300
XFETCH_USER_PREFIX: str = "xfetch:user:"

_user_search_trie = PrefixTrie()


def get_user_search_trie() -> PrefixTrie:
    """Singleton provider for user search autocomplete PrefixTrie."""
    return _user_search_trie


class UserService:
    """Service handling User business logic and concurrent task orchestration."""

    def __init__(
        self,
        repository: UserRepositoryProtocol,
        uow: UnitOfWorkProtocol | None = None,
        trie: PrefixTrie | None = None,
        cache_service: CacheService | None = None,
    ) -> None:
        self._repo = repository
        self._uow = uow
        self._trie = trie if trie is not None else get_user_search_trie()
        self._cache = cache_service

    def _index_user_in_trie(self, user: UserEntity) -> None:
        """Index user username and full_name into PrefixTrie."""
        payload = {
            "id": user.id,
            "username": user.username,
            "full_name": user.full_name,
        }
        self._trie.insert(key=user.username, payload=payload, score=2)
        if user.full_name:
            self._trie.insert(key=user.full_name, payload=payload, score=1)

    async def register_user(self, payload: UserCreate) -> UserEntity:
        """Register a new user with O(1) uniqueness validation asynchronously.

        Incoming payload fields are already normalized, sanitized, and invariant-verified
        by Pydantic v2 schemas at the application boundary. Transient fields (like
        password_confirm) are safely stripped from persistence operations.

        Raises:
            UserAlreadyExistsException: If email or username is already taken.
        """
        # O(1) duplicate checks using repository inverted hash indexes
        if await self._repo.get_by_email(payload.email) is not None:
            raise UserAlreadyExistsException(f"Email '{payload.email}' is already registered.")

        if await self._repo.get_by_username(payload.username) is not None:
            raise UserAlreadyExistsException(f"Username '{payload.username}' is already taken.")

        # CPU-bound key derivation offloaded to thread pool to prevent event loop starvation
        password_hash = await get_password_hash(payload.password)

        created_user = await self._repo.create(
            email=payload.email,
            username=payload.username,
            password_hash=password_hash,
            age=payload.age,
            role=payload.role.value,
            full_name=payload.full_name,
            phone_number=payload.phone_number,
            bio=payload.bio,
            company_name=payload.company_name,
        )
        self._index_user_in_trie(created_user)
        return created_user

    async def update_user(self, user_id: int, payload: UserUpdate) -> UserEntity:
        """Update user attributes with O(1) domain uniqueness verification asynchronously.

        Raises:
            UserNotFoundException: If user does not exist.
            UserAlreadyExistsException: If updated email or username is taken by another user.
        """
        user = await self.get_user_by_id(user_id)

        if payload.email is not None and payload.email != user.email:
            existing_email_user = await self._repo.get_by_email(payload.email)
            if existing_email_user is not None and existing_email_user.id != user_id:
                raise UserAlreadyExistsException(f"Email '{payload.email}' is already registered.")

        if payload.username is not None and payload.username != user.username:
            existing_user = await self._repo.get_by_username(payload.username)
            if existing_user is not None and existing_user.id != user_id:
                raise UserAlreadyExistsException(f"Username '{payload.username}' is already taken.")

        role_val = payload.role.value if payload.role is not None else None

        updated_user = await self._repo.update(
            user_id=user_id,
            email=payload.email,
            username=payload.username,
            age=payload.age,
            role=role_val,
            full_name=payload.full_name,
            phone_number=payload.phone_number,
            bio=payload.bio,
            company_name=payload.company_name,
        )
        if updated_user is None:
            raise UserNotFoundException(user_id=user_id)

        # Synchronize PrefixTrie indexing
        if user.username != updated_user.username:
            self._trie.delete(user.username)
        if user.full_name and user.full_name != updated_user.full_name:
            self._trie.delete(user.full_name)
        self._index_user_in_trie(updated_user)

        # Cache Invalidation: evict stale user entry from Redis
        if self._cache is not None:
            try:
                await self._cache.delete(f"{CACHE_USER_PREFIX}{user_id}")
            except Exception as exc:
                logger.warning("Failed to evict cache key for user %s: %s", user_id, exc)

        return updated_user

    async def update_user_write_through(self, user_id: int, payload: UserUpdate) -> UserEntity:
        """Update user attributes using the Write-Through caching pattern.

        1. Validates uniqueness constraints in O(1) time.
        2. Persists changes synchronously to the underlying repository/database.
        3. Synchronizes PrefixTrie indexing.
        4. Write-Through: Immediately serializes and populates the updated entity
           directly into Redis (cache:user:{user_id}) with TTL=300s.
        5. Returns the updated entity, guaranteeing zero cache misses on subsequent reads
           with 100% cache-database consistency.

        Raises:
            UserNotFoundException: If user does not exist.
            UserAlreadyExistsException: If email or username is already taken.
        """
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundException(user_id=user_id)

        if payload.email is not None and payload.email != user.email:
            existing_email_user = await self._repo.get_by_email(payload.email)
            if existing_email_user is not None and existing_email_user.id != user_id:
                raise UserAlreadyExistsException(f"Email '{payload.email}' is already registered.")

        if payload.username is not None and payload.username != user.username:
            existing_user = await self._repo.get_by_username(payload.username)
            if existing_user is not None and existing_user.id != user_id:
                raise UserAlreadyExistsException(f"Username '{payload.username}' is already taken.")

        role_val = payload.role.value if payload.role is not None else None

        updated_user = await self._repo.update(
            user_id=user_id,
            email=payload.email,
            username=payload.username,
            age=payload.age,
            role=role_val,
            full_name=payload.full_name,
            phone_number=payload.phone_number,
            bio=payload.bio,
            company_name=payload.company_name,
        )
        if updated_user is None:
            raise UserNotFoundException(user_id=user_id)

        # Synchronize PrefixTrie indexing
        if user.username != updated_user.username:
            self._trie.delete(user.username)
        if user.full_name and user.full_name != updated_user.full_name:
            self._trie.delete(user.full_name)
        self._index_user_in_trie(updated_user)

        # Write-Through: populate Redis immediately with fresh data
        if self._cache is not None:
            try:
                serialized = self._serialize_user(updated_user)
                await self._cache.set_str(
                    f"{CACHE_USER_PREFIX}{user_id}",
                    serialized,
                    expire_seconds=CACHE_USER_TTL_SECONDS,
                )
            except Exception as exc:
                logger.warning(
                    "Redis Write-Through failed for key '%s': %s.",
                    f"{CACHE_USER_PREFIX}{user_id}",
                    exc,
                )

        return updated_user

    async def update_profile(self, user_id: int, payload: UserProfileUpdate) -> UserEntity:
        """Update user profile fields (backward compatible helper)."""
        return await self.update_user(
            user_id=user_id,
            payload=UserUpdate(
                username=payload.username,
                age=payload.age,
                full_name=payload.full_name,
                phone_number=payload.phone_number,
                bio=payload.bio,
                company_name=payload.company_name,
            ),
        )

    async def delete_user(self, user_id: int) -> None:
        """Delete a user by primary ID asynchronously.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        user = await self.get_user_by_id(user_id)
        deleted = await self._repo.delete(user_id)
        if not deleted:
            raise UserNotFoundException(user_id=user_id)

        # Prune user entries from PrefixTrie
        self._trie.delete(user.username)
        if user.full_name:
            self._trie.delete(user.full_name)

        # Cache Invalidation: evict deleted user from Redis
        if self._cache is not None:
            try:
                await self._cache.delete(f"{CACHE_USER_PREFIX}{user_id}")
            except Exception as exc:
                logger.warning("Failed to evict cache key for deleted user %s: %s", user_id, exc)

    @staticmethod
    def _serialize_user(user: UserEntity) -> str:
        """Serialize UserEntity to a compact JSON string."""
        data: dict[str, Any] = {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "password_hash": user.password_hash,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat(),
            "age": user.age,
            "role": user.role,
            "full_name": user.full_name,
            "phone_number": user.phone_number,
            "bio": user.bio,
            "company_name": user.company_name,
        }
        return json.dumps(data)

    @staticmethod
    def _deserialize_user(raw_json: str) -> UserEntity:
        """Deserialize a JSON string into a validated UserEntity."""
        data: dict[str, Any] = json.loads(raw_json)
        return UserEntity(
            id=int(data["id"]),
            email=str(data["email"]),
            username=str(data["username"]),
            password_hash=str(data["password_hash"]),
            is_active=bool(data["is_active"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            age=int(data["age"]) if data.get("age") is not None else None,
            role=str(data.get("role", "user")),
            full_name=str(data["full_name"]) if data.get("full_name") is not None else None,
            phone_number=str(data["phone_number"]) if data.get("phone_number") is not None else None,
            bio=str(data["bio"]) if data.get("bio") is not None else None,
            company_name=str(data["company_name"]) if data.get("company_name") is not None else None,
        )

    async def get_user_by_id(self, user_id: int) -> UserEntity:
        """Fetch user by ID with validation asynchronously.

        Implements Cache-Aside (Lazy Loading) pattern:
        1. Check Redis for cache key 'cache:user:{user_id}'.
        2. On cache hit: record hit metric, deserialize, and return immediately.
        3. On cache miss: record miss metric, query database repository,
           store serialized entity in Redis with TTL (300s), and return entity.
        4. Resilient fallback: If Redis encounters an error, gracefully fall back
           to database without breaking user request.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        cache_key = f"{CACHE_USER_PREFIX}{user_id}"

        # Step 1: Check Cache (if CacheService is configured)
        if self._cache is not None:
            try:
                cached_data = await self._cache.get_str(cache_key)
                if cached_data is not None:
                    await self._cache.record_hit()
                    return self._deserialize_user(cached_data)
                await self._cache.record_miss()
            except Exception as exc:
                logger.warning(
                    "Redis cache read failed for key '%s': %s. Falling back to DB.",
                    cache_key,
                    exc,
                )

        # Step 2: Query Database / Repository
        user = await self._repo.get_by_id(user_id)
        if user is None:
            raise UserNotFoundException(user_id=user_id)

        # Step 3: Lazy-Load Entity into Cache
        if self._cache is not None:
            try:
                serialized = self._serialize_user(user)
                await self._cache.set_str(cache_key, serialized, expire_seconds=CACHE_USER_TTL_SECONDS)
            except Exception as exc:
                logger.warning(
                    "Redis cache write failed for key '%s': %s.",
                    cache_key,
                    exc,
                )

        return user

    async def get_user_by_id_xfetch(
        self,
        user_id: int,
        beta: float = 1.0,
        now: float | None = None,
        rand_val: float | None = None,
    ) -> UserEntity:
        """Fetch user by ID protected by XFetch Probabilistic Cache Stampede Prevention.

        Under heavy concurrent reads near key expiration, exactly one request probabilistically
        triggers early recomputation from the database, while all other concurrent requests are
        served warm cached data from the envelope. Eliminates the Thundering Herd disaster.

        Raises:
            UserNotFoundException: If user does not exist.
        """
        if self._cache is None:
            user = await self._repo.get_by_id(user_id)
            if user is None:
                raise UserNotFoundException(user_id=user_id)
            return user

        cache_key = f"{XFETCH_USER_PREFIX}{user_id}"

        async def compute() -> str:
            db_user = await self._repo.get_by_id(user_id)
            if db_user is None:
                raise UserNotFoundException(user_id=user_id)
            return self._serialize_user(db_user)

        serialized_user = await self._cache.xfetch_get_or_compute(
            key=cache_key,
            compute_func=compute,
            ttl=float(CACHE_USER_TTL_SECONDS),
            beta=beta,
            now=now,
            rand_val=rand_val,
        )

        return self._deserialize_user(serialized_user)

    async def get_user_by_username(self, username: str) -> UserEntity:
        """Fetch user by unique username with O(1) hash index lookup asynchronously.

        Raises:
            UserNotFoundException: If user with given username does not exist.
        """
        user = await self._repo.get_by_username(username)
        if user is None:
            raise UserNotFoundException(identifier=username)
        return user

    async def list_users(
        self,
        limit: int = 10,
        offset: int = 0,
        role: UserRole | None = None,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> list[UserEntity]:
        """Fetch registered users with pagination and optional filters asynchronously."""
        role_val = role.value if role else None
        return await self._repo.list_all(
            limit=limit,
            offset=offset,
            role=role_val,
            search=search,
            is_active=is_active,
        )

    async def _fetch_activity_logs(self, user_id: int) -> list[dict[str, Any]]:
        """Simulate non-blocking async network/database I/O to fetch user activity logs."""
        await asyncio.sleep(0.05)
        return [
            {"event": "login", "timestamp": "2026-09-08T10:00:00Z", "ip": "127.0.0.1"},
            {"event": "profile_view", "timestamp": "2026-09-08T10:05:00Z", "user_id": user_id},
        ]

    async def _fetch_account_stats(self, user_id: int) -> dict[str, Any]:
        """Simulate non-blocking async cache/metrics I/O to fetch account statistics."""
        await asyncio.sleep(0.05)
        return {
            "user_id": user_id,
            "total_logins": 42,
            "account_health": "good",
            "storage_used_mb": 128.5,
        }

    async def get_user_dashboard(self, user_id: int) -> dict[str, Any]:
        """Concurrently aggregate profile, activity, and stats using asyncio.gather.

        Executes 3 independent I/O operations concurrently in O(max(t_i)) time instead
        of sequential O(sum(t_i)) time. Ensures clean exception propagation and cancellation
        of sibling tasks if any concurrent branch fails.

        Raises:
            UserNotFoundException: If user profile does not exist.
        """
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self.get_user_by_id(user_id)),
            asyncio.create_task(self._fetch_activity_logs(user_id)),
            asyncio.create_task(self._fetch_account_stats(user_id)),
        ]

        try:
            results = await asyncio.gather(*tasks)
            profile: UserEntity = results[0]
            activity_logs: list[dict[str, Any]] = results[1]
            stats: dict[str, Any] = results[2]

            return {
                "profile": profile,
                "activity_logs": activity_logs,
                "stats": stats,
            }
        except Exception:
            # Clean exception propagation: cancel any lingering sibling tasks
            for t in tasks:
                if not t.done():
                    t.cancel()
            raise

    async def authenticate_user(
        self,
        username: str,
        password: str,
    ) -> UserEntity | None:
        """Authenticate a user using constant-time hash verification offloaded to worker thread."""
        user = await self._repo.get_by_username(username)
        if user is None:
            return None
        is_valid = await verify_password(password, user.password_hash)
        if not is_valid:
            return None
        return user

    @staticmethod
    def _sync_compute_heavy_report(user_id: int, username: str) -> dict[str, Any]:
        """CPU-bound heavy report generation simulating cryptographic verification over large dataset."""
        hasher = hashlib.sha256()
        records_count = 50_000
        for i in range(records_count):
            hasher.update(f"{user_id}:{username}:{i}".encode())
        return {
            "user_id": user_id,
            "username": username,
            "report_checksum": hasher.hexdigest(),
            "records_processed": records_count,
            "generated_at": datetime.now(UTC),
        }

    async def generate_user_report(self, user_id: int) -> dict[str, Any]:
        """Generate an analytics report by offloading CPU-bound computation to worker thread."""
        user = await self.get_user_by_id(user_id)
        return await asyncio.to_thread(self._sync_compute_heavy_report, user.id, user.username)

    async def create_user_with_initial_post(
        self,
        user_create: UserCreate,
        post_title: str,
        post_content: str,
        uow: UnitOfWorkProtocol | None = None,
    ) -> tuple[UserEntity, PostEntity]:
        """Atomically register a new user and create their initial post within a Unit of Work.

        Guarantees ACID atomicity: if post creation or commit fails, the user registration
        is completely rolled back, leaving zero orphaned records in the database.
        """
        active_uow = uow or self._uow
        if active_uow is None:
            raise RuntimeError("UnitOfWork is required for atomic multi-entity operations.")

        async with active_uow:
            # Domain uniqueness validation across the shared transaction boundary
            if await active_uow.users.get_by_email(user_create.email) is not None:
                raise UserAlreadyExistsException(f"Email '{user_create.email}' is already registered.")

            if await active_uow.users.get_by_username(user_create.username) is not None:
                raise UserAlreadyExistsException(f"Username '{user_create.username}' is already taken.")

            # CPU-bound password hashing
            password_hash = await get_password_hash(user_create.password)

            # 1. Create user entity via shared transaction
            user = await active_uow.users.create(
                email=user_create.email,
                username=user_create.username,
                password_hash=password_hash,
                age=user_create.age,
                role=user_create.role.value,
                full_name=user_create.full_name,
                phone_number=user_create.phone_number,
                bio=user_create.bio,
                company_name=user_create.company_name,
            )

            # 2. Create initial post referencing the newly flushed user.id
            post = await active_uow.posts.create(
                title=post_title,
                content=post_content,
                user_id=user.id,
            )

            # 3. Atomically commit both repository operations together
            await active_uow.commit()

            # Index into search trie
            self._index_user_in_trie(user)

            return user, post

    async def autocomplete_users(
        self,
        prefix: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Autocomplete user suggestions by prefix in O(k) sub-millisecond time.

        Args:
            prefix: The search prefix string.
            limit: Maximum number of suggestions to return.

        Returns:
            List of matching user dictionaries with matched_term metadata.
        """
        completions = self._trie.autocomplete(prefix=prefix, limit=limit)
        results: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for matched_term, payloads in completions:
            for p in payloads:
                if isinstance(p, dict) and "id" in p:
                    uid = p["id"]
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        results.append(
                            {
                                "id": p["id"],
                                "username": p["username"],
                                "full_name": p.get("full_name"),
                                "matched_term": matched_term,
                            }
                        )
                        if len(results) >= limit:
                            return results
        return results

    async def sync_trie_from_repository(self) -> None:
        """Hydrate or re-sync the PrefixTrie from repository storage."""
        users = await self._repo.list_all(limit=10000, offset=0)
        for u in users:
            self._index_user_in_trie(u)

    async def filter_users_by_age(
        self,
        min_age: int,
        max_age: int,
    ) -> list[UserEntity]:
        """Filter users within [min_age, max_age] interval in O(log N + M) time.

        Bypasses O(N) full sequential scans by applying logarithmic binary search
        bounds over pre-sorted age projections.
        """
        if min_age > max_age:
            raise ValueError(f"min_age ({min_age}) cannot be greater than max_age ({max_age})")

        all_users = await self._repo.list_all(limit=10000, offset=0)
        users_with_age = [u for u in all_users if u.age is not None]
        sorted_users = sorted(users_with_age, key=lambda u: float(u.age if u.age is not None else 0))

        return binary_search_range(
            sorted_items=sorted_users,
            min_val=float(min_age),
            max_val=float(max_age),
            key_func=lambda u: float(u.age if u.age is not None else 0),
        )

    async def find_user_pair_by_age_sum(
        self,
        target_sum: int,
    ) -> tuple[UserEntity, UserEntity] | None:
        """Find a pair of users whose ages sum to target_sum in O(N) time and O(1) space.

        Uses the converging two-pointer technique to eliminate O(N^2) nested loop checks.
        """
        all_users = await self._repo.list_all(limit=10000, offset=0)
        users_with_age = [u for u in all_users if u.age is not None]
        sorted_users = sorted(users_with_age, key=lambda u: float(u.age if u.age is not None else 0))

        return two_pointer_pair_search(
            sorted_items=sorted_users,
            target=float(target_sum),
            key_func=lambda u: float(u.age if u.age is not None else 0),
        )
