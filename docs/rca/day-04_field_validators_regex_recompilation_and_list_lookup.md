# RCA: Day 04 Regex Re-compilation & $\mathcal{O}(m)$ Keyword List Scans

- **Trigger**: Performance audit during custom `@field_validator` implementation.

---

## 1. Incident 1: Per-Request Regular Expression Re-compilation

### Faulty Code / Pattern
```python
# app/schemas/user.py
@field_validator("phone_number")
@classmethod
def validate_phone(cls, v: Optional[str]) -> Optional[str]:
    # Anti-pattern: Compiling regex on every incoming request
    if v and not re.match(r"^\+[1-9]\d{1,14}$", v):
        raise ValueError("Invalid phone number format")
    return v
```

### Root Cause
Calling `re.match(...)` or `re.compile(...)` inside function bodies incurs repeated regex parsing and compilation overhead on every API call. Under high request throughput, this causes unnecessary CPU churn.

### Resolution
Pre-compiled regular expressions once at module load scope into immutable constants:
```python
# app/schemas/user.py
RE_E164_PHONE: Final[Pattern[str]] = re.compile(r"^\+[1-9]\d{1,14}$")
RE_HTML_TAGS: Final[Pattern[str]] = re.compile(r"<[^>]+>")

@field_validator("phone_number")
@classmethod
def validate_phone(cls, v: Optional[str]) -> Optional[str]:
    if v and not RE_E164_PHONE.match(v):
        raise ValueError("Invalid phone number format")
    return v
```

---

## 2. Incident 2: $\mathcal{O}(m)$ Linear Scans Over Reserved Keyword Lists

### Faulty Code / Pattern
```python
RESERVED_NAMES = ["admin", "root", "system", "superuser", "support"]

if v.lower() in RESERVED_NAMES:  # O(m) linear search over list
    raise ValueError(f"Username '{v}' is reserved.")
```

### Root Cause
Membership testing (`in`) against a Python `list` has an $\mathcal{O}(m)$ time complexity, where $m$ is the list length. As the blocklist expands, validation performance degrades linearly.

### Resolution
Defined reserved word collections as module-level `frozenset` objects to guarantee strictly $\mathcal{O}(1)$ average-time hash lookups:
```python
RESERVED_USERNAMES: Final[frozenset[str]] = frozenset(
    {"admin", "root", "system", "superuser", "support", "administrator", "guest"}
)

if v.lower() in RESERVED_USERNAMES:  # Strictly O(1) hash lookup
    raise ValueError(f"Username '{v}' is reserved.")
```

---

## Permanent Prevention Rules Added to SKILL.md
1. **Module-Scope Regex Pre-Compilation**: Always compile regex patterns using `re.compile()` at module level. Never compile inside functions or validators.
2. **`frozenset` for Blocklists and Lookups**: Always use `frozenset` collections for constant-time $\mathcal{O}(1)$ membership checks.
