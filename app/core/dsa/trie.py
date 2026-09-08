"""Prefix Trie (Prefix Tree) Data Structure with Slotted Nodes for O(k) Autocomplete Search.

Provides:
- TrieNode: Memory-optimized node using __slots__ to eliminate dynamic __dict__ overhead.
- PrefixTrie: Case-insensitive prefix search tree with recursive branch pruning on deletion.
"""

from collections import deque
from typing import Any, Optional


class TrieNode:
    """Memory-optimized Trie Node utilizing __slots__ to suppress dynamic __dict__ bloat."""

    __slots__ = ("children", "frequency", "is_terminal", "payloads")

    def __init__(self) -> None:
        self.children: dict[str, TrieNode] = {}
        self.is_terminal: bool = False
        self.payloads: list[Any] = []
        self.frequency: int = 0


class PrefixTrie:
    """Prefix Trie for ultra-fast, search-as-you-type autocomplete queries.

    Time Complexities:
    - Insert: O(k) where k is the length of the key.
    - Exact Search: O(k) where k is the length of the key.
    - Autocomplete: O(k + m) where k is prefix length and m is the number of visited sub-nodes.
    - Delete with Node Pruning: O(k) where orphan leaf nodes are recursively pruned.
    """

    __slots__ = ("_node_count", "_word_count", "root")

    def __init__(self) -> None:
        self.root: TrieNode = TrieNode()
        self._word_count: int = 0
        self._node_count: int = 1  # Root node

    @property
    def word_count(self) -> int:
        """Total number of unique terminal words in the trie."""
        return self._word_count

    @property
    def node_count(self) -> int:
        """Total number of allocated TrieNode instances in the tree."""
        return self._node_count

    def insert(self, key: str, payload: Any = None, score: int = 1) -> None:
        """Insert a key with an optional associated payload and frequency score.

        Normalizes the key to lowercase. Time complexity: O(k) where k is key length.
        """
        normalized = key.strip().lower()
        if not normalized:
            return

        curr = self.root
        for char in normalized:
            if char not in curr.children:
                curr.children[char] = TrieNode()
                self._node_count += 1
            curr = curr.children[char]

        if not curr.is_terminal:
            curr.is_terminal = True
            self._word_count += 1

        if payload is not None and payload not in curr.payloads:
            curr.payloads.append(payload)

        curr.frequency += max(score, 1)

    def search(self, key: str) -> Optional[list[Any]]:
        """Perform exact match lookup in O(k) time.

        Returns:
            List of payloads associated with the exact key, or None if not found/not terminal.
        """
        normalized = key.strip().lower()
        if not normalized:
            return None

        curr = self.root
        for char in normalized:
            if char not in curr.children:
                return None
            curr = curr.children[char]

        if curr.is_terminal:
            return list(curr.payloads)
        return None

    def autocomplete(
        self,
        prefix: str,
        limit: int = 10,
    ) -> list[tuple[str, list[Any]]]:
        """Locate prefix root in strictly O(k) time and collect top completions.

        Args:
            prefix: The search prefix.
            limit: Maximum number of completions to return.

        Returns:
            List of tuples: (completed_word, payloads), sorted by frequency descending.
        """
        normalized = prefix.strip().lower()
        if not normalized or limit <= 0:
            return []

        # 1. Traverse to prefix root in strictly O(k) time
        curr = self.root
        for char in normalized:
            if char not in curr.children:
                return []  # Prefix does not exist; abort immediately in O(k)
            curr = curr.children[char]

        # 2. Collect all completions below prefix root
        results: list[tuple[str, list[Any], int]] = []
        queue: deque[tuple[TrieNode, str]] = deque([(curr, normalized)])

        while queue:
            node, current_word = queue.popleft()
            if node.is_terminal:
                results.append((current_word, list(node.payloads), node.frequency))

            for char, child_node in node.children.items():
                queue.append((child_node, current_word + char))

        # 3. Sort by frequency score descending, then alphabetically for stability
        results.sort(key=lambda item: (-item[2], item[0]))

        return [(word, payloads) for word, payloads, _ in results[:limit]]

    def delete(self, key: str, payload: Optional[Any] = None) -> bool:
        """Delete a key and recursively prune orphan nodes to prevent memory leaks.

        Args:
            key: The word to remove.
            payload: If provided, only removes this specific payload.
                     If payloads remain, terminal status is kept.

        Returns:
            True if key was found and processed, False otherwise.
        """
        normalized = key.strip().lower()
        if not normalized:
            return False

        def _prune(node: TrieNode, depth: int) -> tuple[bool, bool]:
            """Recursive helper: returns (found_key, can_delete_node)."""
            if depth == len(normalized):
                if not node.is_terminal:
                    return False, False

                if payload is not None:
                    if payload in node.payloads:
                        node.payloads.remove(payload)
                    if len(node.payloads) > 0:
                        # Retain terminal status as other payloads remain attached
                        return True, False

                # Full deletion of terminal status
                node.is_terminal = False
                node.payloads.clear()
                node.frequency = 0
                self._word_count -= 1
                can_delete = len(node.children) == 0
                return True, can_delete

            char = normalized[depth]
            if char not in node.children:
                return False, False

            child = node.children[char]
            found, can_delete_child = _prune(child, depth + 1)

            if can_delete_child:
                del node.children[char]
                self._node_count -= 1
                can_delete_current = len(node.children) == 0 and not node.is_terminal
                return found, can_delete_current

            return found, False

        found, _ = _prune(self.root, 0)
        return found

    def clear(self) -> None:
        """Reset the trie, releasing all child nodes to garbage collection."""
        self.root = TrieNode()
        self._word_count = 0
        self._node_count = 1


__all__ = [
    "PrefixTrie",
    "TrieNode",
]
