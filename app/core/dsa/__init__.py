"""Data Structures & Algorithms core package."""

from app.core.dsa.hash_map import CompactHashMap, Entry, diagnose_hash_health
from app.core.dsa.memory_profiler import measure_memory_footprint
from app.core.dsa.trie import PrefixTrie, TrieNode

__all__ = [
    "CompactHashMap",
    "Entry",
    "PrefixTrie",
    "TrieNode",
    "diagnose_hash_health",
    "measure_memory_footprint",
]
