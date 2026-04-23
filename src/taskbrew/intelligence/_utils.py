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


def validate_path(
    path: str,
    *,
    project_root: str | None = None,
    allow_absolute: bool = True,
) -> str:
    """Validate a file path for safety.

    audit 07a F#3 / 08a F#4-5 / 08b F#3: the previous implementation only
    rejected ``..`` components after ``normpath``. Absolute paths like
    ``/etc/passwd`` normalised unchanged and passed every check; callers
    that joined the returned value back onto their project_root via
    ``Path(root) / result`` had the absolute right operand silently
    overwrite the left, leaking arbitrary-file read.

    Contract:

    - Empty paths are always rejected.
    - ``..`` components are always rejected.
    - When *allow_absolute* is False, absolute paths (Unix ``/...`` and
      Windows drive-letter ``X:\\...``) are refused. Agent-supplied
      paths from untrusted inputs MUST pass ``allow_absolute=False``.
    - When *project_root* is supplied, the path is resolved against the
      root AND required to stay inside it (``is_relative_to``), which
      catches symlink escapes that ``normpath`` alone cannot detect.
      This is the strongest guarantee and callers should prefer it.

    Default ``allow_absolute=True`` preserves the legacy shape for
    operator-trusted callers (e.g. ``learn_conventions`` scans an
    absolute directory handed in by the orchestrator). For agent-facing
    endpoints (intelligence_v2/v3 routers, security_intel file readers,
    code_reasoning analyzers) callers should supply
    ``allow_absolute=False`` and ``project_root=<project_dir>``.
    """
    if not path or not isinstance(path, str):
        raise ValueError("Invalid path: empty")
    normalized = os.path.normpath(path)
    is_abs = os.path.isabs(normalized) or (
        len(normalized) >= 2 and normalized[1] == ":"
    )
    if is_abs and not allow_absolute:
        raise ValueError(f"Absolute paths not allowed: {path!r}")
    parts = normalized.split(os.sep)
    if any(p == ".." for p in parts):
        raise ValueError(f"Path traversal not allowed: {path!r}")
    if project_root is None:
        return normalized

    # Project-root containment check via realpath.
    from pathlib import Path
    root = Path(project_root).resolve()
    if is_abs:
        candidate = Path(normalized).resolve()
    else:
        candidate = (root / normalized).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Path escapes project_root: {path!r} resolves outside {root}"
        ) from exc
    return str(candidate)


def clamp(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    """Clamp a value to [min_val, max_val] range."""
    return max(min_val, min(max_val, value))


# audit 07a F#1: default size cap for file reads in the code analyzers.
# 2 MiB covers every legitimate source file by a wide margin; hostile
# / generated files that exceed this are skipped rather than OOM'ing
# the worker.
_DEFAULT_MAX_READ_BYTES = 2 * 1024 * 1024


def safe_read_text(
    path,
    *,
    max_bytes: int = _DEFAULT_MAX_READ_BYTES,
    errors: str = "replace",
) -> str:
    """Read a text file with a hard size cap.

    Used by code_intel, code_reasoning, security_intel and related
    analyzers that walk source trees and would otherwise read arbitrary
    multi-MB blobs into memory. Returns ``""`` when *path* does not
    resolve to a regular file, is a symlink, or exceeds *max_bytes*.

    The intentional behaviour on oversized files is to silently return
    empty: the analyzer then sees "no patterns" and moves on, instead
    of crashing on a generated asset that happens to live under the
    project root.
    """
    from pathlib import Path
    p = Path(path)
    try:
        # Refuse symlinks so a planted link to /dev/zero or
        # /proc/kcore cannot blow up the analyzer.
        if p.is_symlink():
            logger.debug("safe_read_text: refusing symlink %s", p)
            return ""
        if not p.is_file():
            return ""
        size = p.stat().st_size
    except OSError:
        return ""
    if size > max_bytes:
        logger.warning(
            "safe_read_text: %s is %d bytes (cap %d) — skipped",
            p, size, max_bytes,
        )
        return ""
    try:
        return p.read_text(errors=errors)
    except OSError:
        return ""
