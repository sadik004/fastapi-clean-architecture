"""XFetch (Optimal Probabilistic Early Expiration) Algorithm for Cache Stampede Prevention.

Reference:
    Vattani, A., Chierichetti, F., & Lowenstein, K. (2015).
    "Optimal Probabilistic Cache Stampede Prevention."
    Proceedings of the VLDB Endowment, Vol. 8, No. 8.
"""

from __future__ import annotations

import json
import math
import secrets
import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class XFetchEnvelope:
    """Slotted container wrapping cached data alongside computation duration and target expiry.

    Attributes:
        val: Serialized data payload string.
        delta: Measured computation duration (in seconds) to fetch/generate the value.
        expiry: Target logical expiration timestamp in seconds (epoch timestamp).
    """

    val: str
    delta: float
    expiry: float

    def to_json(self) -> str:
        """Serialize envelope to JSON string in O(1) time."""
        return json.dumps(
            {
                "val": self.val,
                "delta": self.delta,
                "expiry": self.expiry,
            }
        )

    @classmethod
    def from_json(cls, raw_json: str) -> XFetchEnvelope:
        """Deserialize envelope from JSON string in O(1) time."""
        data: dict[str, Any] = json.loads(raw_json)
        return cls(
            val=str(data["val"]),
            delta=float(data["delta"]),
            expiry=float(data["expiry"]),
        )


def should_recompute(
    delta: float,
    expiry: float,
    now: float | None = None,
    beta: float = 1.0,
    rand_val: float | None = None,
) -> bool:
    """Evaluate Vattani's XFetch probabilistic early recomputation condition.

    Condition:
        (- beta * delta * ln(random())) > (expiry - now)

    Args:
        delta: Computation duration in seconds. Must be > 0.
        expiry: Logical target expiration epoch timestamp in seconds.
        now: Current epoch timestamp (defaults to time.time()).
        beta: Aggressiveness multiplier (default 1.0). Greater beta causes earlier recomputation.
        rand_val: Optional uniform random float in (0.0, 1.0] for deterministic unit testing.

    Returns:
        True if the caller should recompute the cached value early, False otherwise.

    Complexity:
        O(1) time complexity (scalar math operations).
        O(1) auxiliary memory.
    """
    current_time = time.time() if now is None else now

    # If already logically expired, must recompute immediately
    if current_time >= expiry:
        return True

    # Guard against non-positive delta or beta
    effective_delta = max(delta, 0.001)
    effective_beta = max(beta, 0.001)

    # Uniform random float in (0, 1]
    if rand_val is None:
        # Cryptographically secure random float in (0.0, 1.0]
        r = (secrets.randbelow(1_000_000_000) + 1) / 1_000_000_000.0
    else:
        # Clamp provided test value to (0.0, 1.0]
        r = max(min(rand_val, 1.0), 1e-9)

    # XFetch condition
    probabilistic_threshold = -effective_beta * effective_delta * math.log(r)
    remaining_ttl = expiry - current_time

    return probabilistic_threshold > remaining_ttl
