# RCA: Day 23 - FastAPI Annotated Query Default Value Trap & Trie Branch Pruning Invariant

- **Date**: 2026-09-08
- **Role**: Junior Apprentice Backend Engineer
- **Lead Architect & Mentor**: User
- **Topic**: FastAPI Dependency Metadata Declarations, Trie Branch Memory Pruning & Route Precedence

---

## 1. Trigger
During Day 23 implementation of `GET /users/autocomplete`:
1. Application startup failed during route registration with:
   ```text
   AssertionError: `Query` default value cannot be set in `Annotated` for 'limit'. Set the default value with `=` instead.
   ```
2. Deletion in Trie structures can easily create memory leaks if orphan leaf nodes are not recursively pruned when terminal words are deleted.
3. If `/users/autocomplete` is registered after `/{user_id}`, FastAPI fails with HTTP 422 attempting to parse `"autocomplete"` as an integer ID.

---

## 2. Faulty Code / Pattern

### Issue A: Conflicting Default Definition in `Annotated` Parameter
```python
# Contradictory default definition
async def autocomplete_users(
    limit: Annotated[
        int,
        Query(
            default=10,  # Fails! Cannot define default inside Query when default is set via = 10
            ge=1,
            le=50,
        ),
    ] = 10,
    ...
)
```
In FastAPI / Pydantic, setting `default=10` inside `Query()` while simultaneously specifying `= 10` on the parameter signature raises an `AssertionError` during FastAPI router initialization (`analyze_param`).

### Issue B: Leaking Orphan Nodes on Word Deletion
```python
# Naive trie deletion
def delete(self, key: str) -> None:
    node = self._traverse_to(key)
    if node:
        node.is_terminal = False  # Leaves entire branch of empty TrieNodes in memory!
```
Simply clearing `is_terminal = False` leaves empty `TrieNode` objects in the tree. Over millions of deleted words, these orphan nodes create significant memory leaks.

---

## 3. Root Cause
1. **FastAPI Metadata Signature Conflict**: FastAPI enforces a strict rule: when using Python's `Annotated[Type, Query(...)] = default`, the default value MUST be declared exclusively in the function signature (`= default`), never inside `Query(default=...)`.
2. **Missing Bottom-Up Pruning**: Prefix Trees are directed acyclic trees. When a word is deleted, any child node that has no children (`len(node.children) == 0`) and is not marked as terminal (`not node.is_terminal`) is dead memory and must be pruned in reverse (bottom-up) order.

---

## 4. Resolution

### Solution A: Canonical Parameter Signature
Removed `default=10` from inside `Query()`:
```python
async def autocomplete_users(
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=50,
            description="Search prefix query string",
        ),
    ],
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=50,
            description="Maximum number of completions to return",
        ),
    ] = 10,
    service: Annotated[UserService, Depends(get_user_service)] = None,
) -> list[UserAutocompleteResponse]:
    ...
```

### Solution B: Recursive Branch Pruning
Implemented bottom-up recursive pruning in `PrefixTrie.delete()`:
```python
def _prune(node: TrieNode, depth: int) -> tuple[bool, bool]:
    if depth == len(normalized):
        if not node.is_terminal:
            return False, False
        node.is_terminal = False
        node.payloads.clear()
        node.frequency = 0
        self._word_count -= 1
        return True, len(node.children) == 0

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
```

### Solution C: Route Precedence
Placed `/users/autocomplete` strictly before `/{user_id}`, adhering to Good Pattern 28.

---

## 5. Permanent Prevention Rule
1. **Annotated Query Default Rule**: Never pass `default=...` inside `Query()` when using `Annotated[Type, Query(...)] = default`.
2. **Trie Pruning Rule**: In-memory trees must implement bottom-up recursive pruning on deletion, asserting that `node_count` decreases and returns to 1 when all words are deleted.
3. **Literal Sub-Path Precedence**: All literal routes (`/autocomplete`, `/me`, `/metrics`) must precede parameter routes (`/{user_id}`).
