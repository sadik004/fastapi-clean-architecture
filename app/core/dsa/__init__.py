"""Data Structures & Algorithms core package."""

from app.core.dsa.bloom_filter import BloomFilter
from app.core.dsa.distributed_lock import DistributedLock
from app.core.dsa.hash_map import CompactHashMap, Entry, diagnose_hash_health
from app.core.dsa.memory_profiler import measure_memory_footprint
from app.core.dsa.priority_queue import (
    JobPriority,
    PriorityJob,
    PriorityJobScheduler,
)
from app.core.dsa.search_algorithms import (
    binary_search_bounds,
    binary_search_range,
    two_pointer_pair_search,
)
from app.core.dsa.sliding_window import SlidingWindowLog
from app.core.dsa.trie import PrefixTrie, TrieNode

__all__ = [
    "BloomFilter",
    "CompactHashMap",
    "DistributedLock",
    "Entry",
    "JobPriority",
    "PrefixTrie",
    "PriorityJob",
    "PriorityJobScheduler",
    "SlidingWindowLog",
    "TrieNode",
    "binary_search_bounds",
    "binary_search_range",
    "diagnose_hash_health",
    "measure_memory_footprint",
    "two_pointer_pair_search",
]
