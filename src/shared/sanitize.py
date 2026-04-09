"""
Input-sanitisation helpers shared across modules.

Centralises rules for safe external identifiers (used as file names,
path segments, query parameters, etc.).
"""

from __future__ import annotations

import re

# Safe identifier: 1-128 chars, alphanumeric plus hyphens, underscores, dots.
_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9\-_.]{1,128}$")


def is_safe_id(value: str) -> bool:
    """Return ``True`` if *value* is a safe external identifier."""
    return bool(_SAFE_ID_PATTERN.match(value))


def require_safe_id(value: str, label: str = "id") -> str:
    """
    Return *value* unchanged if it passes the safe-id check.

    Raises ``ValueError`` with a user-friendly message otherwise.
    """
    if not is_safe_id(value):
        raise ValueError(
            f"{label} must be 1-128 characters: alphanumeric, hyphens, "
            f"underscores, or dots."
        )
    return value
