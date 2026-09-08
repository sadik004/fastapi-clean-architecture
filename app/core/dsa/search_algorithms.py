"""Pure high-throughput search algorithms: Binary Search and Two-Pointer technique.

Provides logarithmic O(log N) interval range filtering and O(N) converging pair
target matching over pre-sorted datasets, completely bypassing O(N) linear scans
and O(N^2) nested loop patterns.
"""

from collections.abc import Callable, Sequence
from typing import Optional, TypeVar

T = TypeVar("T")


def binary_search_bounds(
    sorted_items: Sequence[T],
    min_val: float,
    max_val: float,
    key_func: Callable[[T], float],
) -> tuple[int, int]:
    """Find the [left_idx, right_idx) index slice bounding items within [min_val, max_val].

    Uses logarithmic bisection to compute the lower bound and upper bound in
    strictly O(log N) time complexity.

    Args:
        sorted_items: A sequence pre-sorted ascending by `key_func`.
        min_val: The inclusive lower boundary of the range.
        max_val: The inclusive upper boundary of the range.
        key_func: Monotonic projection mapping element T to a numeric float.

    Returns:
        A tuple `(left_idx, right_idx)` defining the half-open slice
        `sorted_items[left_idx:right_idx]`. If no matching elements exist,
        `left_idx >= right_idx` (slice produces an empty sequence).
    """
    n = len(sorted_items)
    if n == 0 or min_val > max_val:
        return 0, 0

    # 1. Locate lower bound (first element where key_func(item) >= min_val) in O(log N)
    low = 0
    high = n
    while low < high:
        mid = (low + high) // 2
        if key_func(sorted_items[mid]) < min_val:
            low = mid + 1
        else:
            high = mid
    left_idx = low

    # If all items are strictly less than min_val, range is empty
    if left_idx == n:
        return n, n

    # 2. Locate upper bound (first element where key_func(item) > max_val) in O(log N)
    # Optimization: upper bound search can start from left_idx rather than 0
    low = left_idx
    high = n
    while low < high:
        mid = (low + high) // 2
        if key_func(sorted_items[mid]) <= max_val:
            low = mid + 1
        else:
            high = mid
    right_idx = low

    return left_idx, right_idx


def binary_search_range(
    sorted_items: Sequence[T],
    min_val: float,
    max_val: float,
    key_func: Callable[[T], float],
) -> list[T]:
    """Extract all items whose projected key falls within [min_val, max_val].

    Finds the boundary indices in strictly O(log N) time and returns the sliced
    sublist in O(log N + M) time, where M is the number of matched elements.

    Args:
        sorted_items: A sequence pre-sorted ascending by `key_func`.
        min_val: The inclusive lower boundary of the range.
        max_val: The inclusive upper boundary of the range.
        key_func: Monotonic projection mapping element T to a numeric float.

    Returns:
        List of matching elements in the range [min_val, max_val].
    """
    left_idx, right_idx = binary_search_bounds(sorted_items, min_val, max_val, key_func)
    if left_idx >= right_idx:
        return []
    return list(sorted_items[left_idx:right_idx])


def two_pointer_pair_search(
    sorted_items: Sequence[T],
    target: float,
    key_func: Callable[[T], float],
    tolerance: float = 1e-9,
) -> Optional[tuple[T, T]]:
    """Find a pair of distinct elements whose combined metric sums to `target`.

    Executes in strictly O(N) time and O(1) auxiliary space using two converging
    pointers moving inward from both ends of the pre-sorted sequence. Completely
    eliminates O(N^2) nested loop scans.

    Args:
        sorted_items: A sequence pre-sorted ascending by `key_func`.
        target: The desired sum of the two elements' projected metrics.
        key_func: Monotonic projection mapping element T to a numeric float.
        tolerance: Allowed floating-point delta for equality comparison.

    Returns:
        A 2-tuple `(item_left, item_right)` if a matching pair is found;
        otherwise `None`.
    """
    n = len(sorted_items)
    if n < 2:
        return None

    left = 0
    right = n - 1

    while left < right:
        val_left = key_func(sorted_items[left])
        val_right = key_func(sorted_items[right])
        current_sum = val_left + val_right
        delta = current_sum - target

        if abs(delta) <= tolerance:
            return sorted_items[left], sorted_items[right]
        if current_sum < target:
            left += 1
        else:
            right -= 1

    return None
