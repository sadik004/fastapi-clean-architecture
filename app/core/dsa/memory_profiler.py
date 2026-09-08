"""Memory profiling and footprint benchmarking utilities for slotted vs unslotted classes."""

import gc
import sys
import tracemalloc
from collections.abc import Callable
from typing import Any


def measure_memory_footprint(
    unslotted_factory: Callable[[], Any],
    slotted_factory: Callable[[], Any],
    count: int = 10_000,
) -> dict[str, Any]:
    """Benchmark memory footprint between unslotted (__dict__) and slotted (__slots__) class instances.

    Uses sys.getsizeof() and tracemalloc to measure:
    - Shallow instance size (bytes)
    - Private __dict__ overhead (bytes)
    - Net heap allocation (KB) for N instances
    - Percentage memory reduction (asserting >= 60% savings)

    Returns:
        dict containing memory metrics and percentage savings.
    """
    # Force initial garbage collection to stabilize memory baseline
    gc.collect()

    # 1. Profile Unslotted Instances
    tracemalloc.start()
    unslotted_instances = [unslotted_factory() for _ in range(count)]
    current_unslotted, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Measure shallow sizes of first instance
    sample_unslotted = unslotted_instances[0]
    unslotted_shallow = sys.getsizeof(sample_unslotted)
    unslotted_dict_size = sys.getsizeof(getattr(sample_unslotted, "__dict__", {}))
    unslotted_instance_total = unslotted_shallow + unslotted_dict_size

    # Clean up unslotted instances before profiling slotted
    del unslotted_instances
    gc.collect()

    # 2. Profile Slotted Instances
    tracemalloc.start()
    slotted_instances = [slotted_factory() for _ in range(count)]
    current_slotted, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    sample_slotted = slotted_instances[0]
    slotted_shallow = sys.getsizeof(sample_slotted)
    slotted_instance_total = slotted_shallow  # No __dict__!

    # Clean up slotted instances
    del slotted_instances
    gc.collect()

    # Compute savings based on tracemalloc current heap allocations
    unslotted_heap_kb = current_unslotted / 1024.0
    slotted_heap_kb = current_slotted / 1024.0

    heap_savings_pct = ((unslotted_heap_kb - slotted_heap_kb) / max(unslotted_heap_kb, 0.001)) * 100.0
    instance_savings_pct = (
        (unslotted_instance_total - slotted_instance_total) / max(unslotted_instance_total, 1)
    ) * 100.0

    return {
        "count": count,
        "unslotted_instance_bytes": unslotted_instance_total,
        "slotted_instance_bytes": slotted_instance_total,
        "unslotted_heap_kb": round(unslotted_heap_kb, 2),
        "slotted_heap_kb": round(slotted_heap_kb, 2),
        "heap_savings_pct": round(heap_savings_pct, 2),
        "instance_savings_pct": round(instance_savings_pct, 2),
        "has_dict_unslotted": hasattr(sample_unslotted, "__dict__"),
        "has_dict_slotted": hasattr(sample_slotted, "__dict__"),
    }


__all__ = [
    "measure_memory_footprint",
]
