"""Domain service orchestrating real-time leaderboards using Redis Sorted Sets (ZSET)."""

from __future__ import annotations

import logging

from app.schemas.leaderboard import LeaderboardEntry, PlayerStanding
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)


class LeaderboardService:
    """High-concurrency Real-Time Leaderboard Service powered by Redis Sorted Sets (ZSET).

    Eliminates relational database 'ORDER BY score DESC' bottlenecks by utilizing:
      - Skip Lists for O(log N) dynamic rank insertion, updates, and rank lookups.
      - Internal Hash Maps for O(1) direct member score queries.
    """

    def __init__(self, cache_service: CacheService) -> None:
        self._cache = cache_service

    @staticmethod
    def _leaderboard_key(leaderboard_id: str) -> str:
        """Construct the isolated Redis key namespace for a given leaderboard scope."""
        return f"leaderboard:{leaderboard_id}"

    async def record_score(
        self,
        leaderboard_id: str,
        player_id: str,
        score_delta: float,
    ) -> tuple[float, int]:
        """Atomically increment a player's score and return their updated score and 1-indexed rank.

        Complexity: O(log N) time complexity via Redis ZINCRBY and ZREVRANK.
        """
        key = self._leaderboard_key(leaderboard_id)
        new_score = await self._cache.zincrby(key, amount=score_delta, member=player_id)
        zero_rank = await self._cache.zrevrank(key, member=player_id)
        rank = (zero_rank + 1) if zero_rank is not None else 1
        return new_score, rank

    async def get_top_players(
        self,
        leaderboard_id: str,
        limit: int = 10,
        offset: int = 0,
    ) -> list[LeaderboardEntry]:
        """Retrieve a paginated slice of top-ranked competitors ordered from highest score to lowest.

        Translates 0-indexed Redis ZREVRANGE positions into human-readable 1-indexed ranks.
        Complexity: O(log N + M) where M is the count of elements retrieved.
        """
        if limit <= 0:
            return []

        start = offset
        stop = offset + limit - 1
        key = self._leaderboard_key(leaderboard_id)
        items = await self._cache.zrevrange_with_scores(key, start=start, stop=stop)

        return [
            LeaderboardEntry(
                rank=offset + index + 1,
                player_id=player,
                score=score,
            )
            for index, (player, score) in enumerate(items)
        ]

    async def get_player_standing(
        self,
        leaderboard_id: str,
        player_id: str,
    ) -> PlayerStanding | None:
        """Query a player's exact score, 1-indexed rank, total competitors, and percentile standing.

        Percentile calculation:
          - If total_players > 1: round(((total_players - rank) / total_players) * 100, 2)
          - If total_players == 1: 100.0 (top standing)
        Complexity: O(log N) for rank + O(1) for score and cardinality.
        """
        key = self._leaderboard_key(leaderboard_id)
        score = await self._cache.zscore(key, player_id)
        if score is None:
            return None

        zero_rank = await self._cache.zrevrank(key, player_id)
        if zero_rank is None:
            return None

        rank = zero_rank + 1
        total_players = await self._cache.zcard(key)

        percentile: float
        if total_players > 1:
            percentile = round(((total_players - rank) / total_players) * 100.0, 2)
        else:
            percentile = 100.0

        return PlayerStanding(
            leaderboard_id=leaderboard_id,
            player_id=player_id,
            score=score,
            rank=rank,
            total_players=total_players,
            percentile=percentile,
        )

    async def remove_player(self, leaderboard_id: str, player_id: str) -> bool:
        """Evict a player from the leaderboard.

        Complexity: O(log N) time complexity.
        """
        key = self._leaderboard_key(leaderboard_id)
        removed_count = await self._cache.zrem(key, player_id)
        return removed_count > 0

    async def get_total_players(self, leaderboard_id: str) -> int:
        """Return the total number of competitors on the specified leaderboard.

        Complexity: O(1) time complexity via ZCARD.
        """
        return await self._cache.zcard(self._leaderboard_key(leaderboard_id))

    async def clear_leaderboard(self, leaderboard_id: str) -> bool:
        """Delete an entire leaderboard key from Redis.

        Complexity: O(N) where N is the number of keys deleted (here, N=1).
        """
        key = self._leaderboard_key(leaderboard_id)
        deleted = await self._cache.delete(key)
        return deleted > 0
