# RCA: Day 03 Repository Secondary Index Desynchronization & Ghost Keys

- **Trigger**: Integration test failure during user update and re-registration after deletion.

---

## 1. Incident 1: Ghost Index Keys Blocking Re-Registration After Deletion

### Faulty Code / Pattern
```python
# app/repositories/user_repository.py
def delete(self, user_id: int) -> bool:
    if user_id in self._store:
        del self._store[user_id]  # Anti-pattern: failed to purge secondary indexes!
        return True
    return False
```

### Root Cause
Deleting an entity only from primary storage `_store` left dangling entries in `_email_index` and `_username_index`. When a new or deleted user later attempted to register with the same email or username, the system raised a false `409 Conflict` because the secondary indexes pointed to a phantom ID.

### Resolution
Strictly purged both secondary indexes upon deletion:
```python
# app/repositories/user_repository.py
def delete(self, user_id: int) -> bool:
    user = self._store.get(user_id)
    if user is None:
        return False
    self._email_index.pop(user.email, None)
    self._username_index.pop(user.username, None)
    del self._store[user_id]
    return True
```

---

## 2. Incident 2: Stale Inverted Index Mappings on Entity Update

### Faulty Code / Pattern
```python
# app/repositories/user_repository.py
def update(self, user_id: int, email: Optional[str] = None, ...):
    user = self._store.get(user_id)
    if email:
        user.email = email  # Anti-pattern: old email remains mapped to user.id!
```

### Root Cause
Modifying `user.email` without removing `old_email` from `_email_index` caused two distinct emails to point to the same user ID, while the old email could never be reclaimed by another account.

### Resolution
Synchronized inverted indexes by evicting the previous key before assigning the new mapping:
```python
if email is not None and email != user.email:
    self._email_index.pop(user.email, None)
    self._email_index[email] = user.id
    user.email = email
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Bidirectional Inverted Index Synchronization**: Whenever a unique attribute changes, purge the old key from secondary indexes in $\mathcal{O}(1)$ time before inserting the new key.
2. **Comprehensive Secondary Index Purging on Delete**: Every `delete()` operation must atomicly purge all secondary lookup references to prevent memory leaks and ghost collisions.
