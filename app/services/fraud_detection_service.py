"""Real-Time Fraud Detection & Anomaly Velocity Engine for Double-Entry Ledger.

Guarantees:
1. Sub-2ms Latency: Executes in-memory Redis Sorted Set (ZSET) sliding-window calculations.
2. Zero Dual-Write Exposure: Evaluated before ledger locking or database transaction initiation.
3. Multi-Rule Composite Risk Scoring:
   - Rule 1: Sliding-window velocity check (count >= 3 or volume > $100,000 in 5 min) -> +40 points.
   - Rule 2: High-value transaction spike (single transfer >= $50,000) -> +30 points.
   - Rule 3: Destination account blacklist check (sanctioned/mule account) -> +100 points.
4. Decision Thresholds:
   - Score < 30: APPROVED.
   - 30 <= Score < 70: FLAGGED_FOR_REVIEW (Transaction proceeds with is_flagged=True).
   - Score >= 70: REJECTED (Raises FraudDetectedException HTTP 403, aborting transfer).
5. Clean Architecture Decoupling: Zero FastAPI transport dependencies; pure domain logic.
6. Standalone Fallback: Fully functional in-memory fallback for containerless unit testing.
"""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal
from uuid import UUID

from redis.asyncio import Redis

from app.schemas.ledger import FraudAssessmentResult

logger = logging.getLogger("app.services.fraud_detection")

# Constants
DEFAULT_VELOCITY_WINDOW_SECONDS: int = 300  # 5 minutes lookback
DEFAULT_VELOCITY_TTL_SECONDS: int = 600  # 10 minutes retention
BURST_COUNT_THRESHOLD: int = 3
BURST_VOLUME_THRESHOLD: Decimal = Decimal("100000.0000")
SPIKE_AMOUNT_THRESHOLD: Decimal = Decimal("50000.0000")

REDIS_BLACKLIST_KEY = "blacklist:accounts"


class FraudDetectionService:
    """Enterprise Real-Time Fraud Detection & Velocity Engine."""

    def __init__(self, redis: Redis | None = None) -> None:
        self.redis = redis
        # In-memory fallbacks for test isolation and zero external dependency resilience
        self._in_memory_velocity: dict[str, list[tuple[float, Decimal]]] = {}
        self._in_memory_blacklist: set[str] = set()

    async def evaluate_transfer(
        self,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Decimal,
        currency: str = "USD",  # noqa: ARG002
    ) -> FraudAssessmentResult:
        """Evaluate real-time fraud risk score and decision policy for a proposed transfer.

        Must execute in < 2ms using Redis in-memory data structures.
        """
        risk_score = 0
        violated_rules: list[str] = []

        # ---------------------------------------------------------
        # Rule 3: Destination Account Blacklist Screening
        # ---------------------------------------------------------
        is_blacklisted = await self.is_account_blacklisted(destination_account_id)
        if is_blacklisted:
            risk_score += 100
            violated_rules.append("DESTINATION_ACCOUNT_BLACKLISTED")

        # ---------------------------------------------------------
        # Rule 1: Sliding-Window Velocity Rate Check (Redis ZSET)
        # ---------------------------------------------------------
        window_count, window_volume = await self.get_account_velocity(
            account_id=source_account_id,
            lookback_seconds=DEFAULT_VELOCITY_WINDOW_SECONDS,
        )

        projected_volume = window_volume + amount

        # Frequency Burst Check
        has_count_burst = window_count >= BURST_COUNT_THRESHOLD
        # Volume Burst Check
        has_volume_burst = projected_volume > BURST_VOLUME_THRESHOLD

        if has_count_burst:
            risk_score += 40
            violated_rules.append("VELOCITY_BURST_EXCEEDED")

        if has_volume_burst:
            if "VELOCITY_BURST_EXCEEDED" not in violated_rules:
                risk_score += 40
                violated_rules.append("VELOCITY_BURST_EXCEEDED")
            else:
                # Both frequency and volume exceeded
                risk_score += 40
                violated_rules.append("VELOCITY_VOLUME_EXCEEDED")

        # ---------------------------------------------------------
        # Rule 2: Sudden Transaction Spike Check
        # ---------------------------------------------------------
        if amount >= SPIKE_AMOUNT_THRESHOLD:
            risk_score += 30
            violated_rules.append("HIGH_VALUE_SPIKE")

        # ---------------------------------------------------------
        # Decision Threshold Policy
        # ---------------------------------------------------------
        if risk_score >= 70:
            decision = "REJECTED"
        elif risk_score >= 30:
            decision = "FLAGGED_FOR_REVIEW"
        else:
            decision = "APPROVED"

        logger.info(
            "Fraud assessment completed for transfer source=%s dest=%s amount=%s: score=%d decision=%s rules=%s",
            source_account_id,
            destination_account_id,
            amount,
            risk_score,
            decision,
            violated_rules,
        )

        return FraudAssessmentResult(
            risk_score=risk_score,
            decision=decision,
            violated_rules=violated_rules,
        )

    async def record_successful_transfer(
        self,
        source_account_id: UUID,
        amount: Decimal,
    ) -> None:
        """Record an atomically committed transfer in the account's velocity sorted set."""
        now = time.time()
        member = f"{amount}:{uuid.uuid4().hex}"
        key = f"velocity:transfers:{source_account_id}"

        if self.redis is not None:
            try:
                pipe = self.redis.pipeline()
                pipe.zadd(key, {member: now})
                pipe.expire(key, DEFAULT_VELOCITY_TTL_SECONDS)
                await pipe.execute()
                return
            except Exception as exc:
                logger.warning(
                    "Redis error while recording transfer velocity: %s. Falling back to in-memory.",
                    exc,
                )

        # In-memory fallback
        acc_key = str(source_account_id)
        if acc_key not in self._in_memory_velocity:
            self._in_memory_velocity[acc_key] = []
        self._in_memory_velocity[acc_key].append((now, amount))

    async def get_account_velocity(
        self,
        account_id: UUID,
        lookback_seconds: int = DEFAULT_VELOCITY_WINDOW_SECONDS,
    ) -> tuple[int, Decimal]:
        """Inspect sliding-window transfer count and cumulative amount within lookback horizon."""
        now = time.time()
        window_start = now - lookback_seconds
        key = f"velocity:transfers:{account_id}"

        if self.redis is not None:
            try:
                # 1. Purge expired entries older than the lookback window
                await self.redis.zremrangebyscore(key, "-inf", window_start)

                # 2. Retrieve remaining members within active window
                raw_entries = await self.redis.zrange(key, 0, -1)
                count = len(raw_entries)
                cumulative_sum = Decimal("0.0000")

                for entry in raw_entries:
                    try:
                        entry_str = entry.decode("utf-8") if isinstance(entry, bytes) else str(entry)
                        amount_str = entry_str.split(":", 1)[0]
                        cumulative_sum += Decimal(amount_str)
                    except (ValueError, IndexError) as parse_err:
                        logger.warning("Failed to parse velocity entry '%s': %s", entry, parse_err)

                return count, cumulative_sum
            except Exception as exc:
                logger.warning(
                    "Redis error during get_account_velocity: %s. Falling back to in-memory.",
                    exc,
                )

        # In-memory fallback
        acc_key = str(account_id)
        if acc_key not in self._in_memory_velocity:
            return 0, Decimal("0.0000")

        # Filter active entries
        active_entries = [(ts, amt) for ts, amt in self._in_memory_velocity[acc_key] if ts >= window_start]
        self._in_memory_velocity[acc_key] = active_entries
        count = len(active_entries)
        cumulative_sum = sum((amt for _, amt in active_entries), Decimal("0.0000"))
        return count, cumulative_sum

    async def blacklist_account(self, account_id: UUID, reason: str = "") -> None:
        """Add an account ID to the destination fraud blacklist."""
        acc_str = str(account_id)
        self._in_memory_blacklist.add(acc_str)

        if self.redis is not None:
            try:
                await self.redis.sadd(REDIS_BLACKLIST_KEY, acc_str)
            except Exception as exc:
                logger.warning("Redis error during blacklist_account: %s", exc)

        logger.warning("Account '%s' added to fraud blacklist. Reason: %s", acc_str, reason)

    async def unblacklist_account(self, account_id: UUID) -> None:
        """Remove an account ID from the destination fraud blacklist."""
        acc_str = str(account_id)
        self._in_memory_blacklist.discard(acc_str)

        if self.redis is not None:
            try:
                await self.redis.srem(REDIS_BLACKLIST_KEY, acc_str)
            except Exception as exc:
                logger.warning("Redis error during unblacklist_account: %s", exc)

    async def is_account_blacklisted(self, account_id: UUID) -> bool:
        """Check if an account ID is currently blacklisted."""
        acc_str = str(account_id)
        if acc_str in self._in_memory_blacklist:
            return True

        if self.redis is not None:
            try:
                return bool(await self.redis.sismember(REDIS_BLACKLIST_KEY, acc_str))
            except Exception as exc:
                logger.warning("Redis error during is_account_blacklisted: %s", exc)

        return False
