"""High-Performance Bitmasking Role-Based Access Control (RBAC) Engine.

Evaluates user permissions in strictly O(1) CPU clock cycles using binary
bitwise operators (&, |, ~). Zero database joins or lookups required.
"""

from enum import IntFlag


class Permission(IntFlag):
    """Atomic binary permission flags represented as powers of 2 (2^n)."""

    NONE = 0
    READ = 1 << 0  # 1  (00000001)
    WRITE = 1 << 1  # 2  (00000010)
    DELETE = 1 << 2  # 4  (00000100)
    ADMIN = 1 << 3  # 8  (00001000)
    EXPORT = 1 << 4  # 16 (00010000)
    BILLING = 1 << 5  # 32 (00100000)


# Composite Role Bitmasks
ROLE_GUEST: int = Permission.READ.value  # 1
ROLE_USER: int = (Permission.READ | Permission.WRITE).value  # 3
ROLE_MODERATOR: int = (Permission.READ | Permission.WRITE | Permission.DELETE).value  # 7
ROLE_ADMIN: int = (
    Permission.READ
    | Permission.WRITE
    | Permission.DELETE
    | Permission.ADMIN
    | Permission.EXPORT
    | Permission.BILLING
).value  # 63


def has_permission(user_perms: int, required_perm: Permission | int) -> bool:
    """Evaluate whether user bitmask contains required permission flag.

    Executes in strictly O(1) time via single CPU bitwise AND comparison.
    """
    req = required_perm.value if isinstance(required_perm, Permission) else required_perm
    return (user_perms & req) == req


def grant_permission(user_perms: int, perm: Permission | int) -> int:
    """Grant permission flag(s) to a user bitmask via bitwise OR.

    Executes in strictly O(1) time without mutating original state.
    """
    flag = perm.value if isinstance(perm, Permission) else perm
    return user_perms | flag


def revoke_permission(user_perms: int, perm: Permission | int) -> int:
    """Revoke permission flag(s) from a user bitmask via bitwise AND NOT.

    Executes in strictly O(1) time without mutating original state.
    """
    flag = perm.value if isinstance(perm, Permission) else perm
    return user_perms & ~flag


def get_permission_names(perms: int) -> list[str]:
    """Extract human-readable permission flag names from an integer bitmask."""
    names: list[str] = []
    for flag in Permission:
        if flag != Permission.NONE and flag.name is not None and (perms & flag.value) == flag.value:
            names.append(flag.name)
    return names
