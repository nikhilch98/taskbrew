"""Shared utilities for intelligence modules."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def new_id(length: int = 12) -> str:
    """Generate a short unique ID."""
    return uuid.uuid4().hex[:length]


def validate_path(path: str) -> str:
    """Validate a file/directory path to prevent directory traversal.

    Raises ValueError if the path contains '..' components.
    """
    normalized = os.path.normpath(path)
    if ".." in normalized.split(os.sep):
        raise ValueError(f"Path traversal not allowed: {path!r}")
    return normalized


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp a value to [min_val, max_val] range."""
    return max(min_val, min(max_val, value))
