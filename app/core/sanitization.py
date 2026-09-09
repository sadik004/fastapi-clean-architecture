"""Input Sanitization & Path Traversal Injection Defense.

Guarantees defense-in-depth against Directory Traversal (CWE-22) and Null Byte
Injections (CWE-626) by neutralizing path manipulation vectors and validating input
parameters before disk or resource interactions.
"""

from __future__ import annotations

import os
import re

from app.core.exceptions import PathTraversalException

# Pre-compiled regex for stripping path manipulation sequences
_TRAVERSAL_PATTERN = re.compile(r"(\.\.[/\\]|[/\\])")


def sanitize_file_path(filename: str, strict: bool = False) -> str:
    """Sanitize and neutralize user-supplied file names or paths.

    Defenses:
    1. Null Byte Rejection: Blocks '\\x00' which attackers use to truncate file extensions.
    2. Control Character Rejection: Blocks non-printable control characters (ASCII 0-31).
    3. Traversal Sequence Neutralization: Strips or rejects '../', '..\\', '/', and '\\'.
    4. Safe Basename Extraction: Extracts canonical basename via os.path.basename.

    Args:
        filename: User-supplied filename or path string.
        strict: If True, raises PathTraversalException immediately upon detecting traversal sequences.
                If False, cleans and sanitizes the filename to a safe basename.

    Returns:
        The clean, sanitized filename string.

    Raises:
        PathTraversalException: If null bytes, control characters, or illegal traversal patterns are found.
    """
    if not filename or not isinstance(filename, str):
        raise PathTraversalException("Filename cannot be empty.")

    # Defense 1: Reject Null Byte injections
    if "\x00" in filename:
        raise PathTraversalException(
            "Security violation: Null byte injection (\\x00) detected in filename."
        )

    # Defense 2: Reject control characters (ASCII < 32)
    for ch in filename:
        if ord(ch) < 32:
            raise PathTraversalException(
                f"Security violation: Prohibited ASCII control character (code {ord(ch)}) detected in filename."
            )

    # Defense 3: Check traversal sequences
    has_traversal = (
        "../" in filename
        or "..\\" in filename
        or "/.." in filename
        or "\\.." in filename
        or filename.startswith("/")
        or filename.startswith("\\")
    )

    if strict and has_traversal:
        raise PathTraversalException(
            f"Security violation: Directory traversal sequence detected in filename '{filename}'."
        )

    # Defense 4: Extract basename and strip remaining separators
    base = os.path.basename(filename.replace("\\", "/"))
    clean = _TRAVERSAL_PATTERN.sub("", base).strip()

    if not clean or all(c == "." for c in clean):
        raise PathTraversalException(
            f"Security violation: Invalid filename '{filename}' produces empty or purely dotted target after sanitization."
        )

    return clean
