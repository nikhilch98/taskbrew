"""Optional context compression via Headroom (lazy, flag-gated, fail-safe).

Thin adapter around the bundled ``headroom-ai`` dependency (a pinned core
dependency in ``pyproject.toml``, so it ships with taskbrew — no separate
install). It is **enabled by default** and engages whenever the ``headroom``
package is importable. Because it is fully fail-safe (see below), default-on is
safe: if the import is ever unavailable (e.g. an unsupported platform where the
wheel did not load) it is simply a no-op. Operators opt out explicitly with
``TASKBREW_COMPRESSION=0`` (or ``false``/``no``/``off``).

When disabled, unavailable, the input is too small, savings are non-positive, or
anything raises, every entry point returns the input **unchanged**. The
orchestrator therefore behaves identically whether or not the dependency loads
— it is used automatically when present and degrades to passthrough otherwise.

With the lightweight pinned install (``headroom-ai`` core only, no ``[ml]``
extra), Headroom runs *structural* compression (SmartCrusher/JSON + CacheAligner)
and leaves prose/instructions untouched — it mainly crushes embedded JSON and
repetitive tool-output dumps, which is exactly where assembled agent context
bloats. Installing the ``[ml]`` extra additionally enables prose compression.

Env knobs:
  * ``TASKBREW_COMPRESSION``           — set to a falsy value to opt out (default on)
  * ``TASKBREW_COMPRESSION_MIN_CHARS`` — skip blobs smaller than this (default 2000)
  * ``TASKBREW_COMPRESSION_MODEL``     — model name for token counting (default sonnet)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
_TRUTHY = {"1", "true", "yes", "on"}

# Lazy-import cache. ``False`` = not yet attempted, ``None`` = unavailable,
# otherwise the imported ``headroom.compress`` callable.
_compress_fn: Callable[..., Any] | None | bool = False


def is_enabled() -> bool:
    """True unless the operator has explicitly opted out.

    On by default: an unset ``TASKBREW_COMPRESSION`` enables compression. Any
    set value is honoured literally (truthy enables, anything else disables),
    so ``TASKBREW_COMPRESSION=0`` turns it off. Safe as a default because the
    adapter no-ops when the headroom dependency is absent.
    """
    val = os.environ.get("TASKBREW_COMPRESSION")
    if val is None:
        return True
    return val.strip().lower() in _TRUTHY


def _min_chars() -> int:
    try:
        return int(os.environ.get("TASKBREW_COMPRESSION_MIN_CHARS", "2000"))
    except ValueError:
        return 2000


def _model_for_counting(explicit: str | None) -> str:
    return explicit or os.environ.get("TASKBREW_COMPRESSION_MODEL") or _DEFAULT_MODEL


def _load_compress() -> Callable[..., Any] | None:
    """Import ``headroom.compress`` once, caching the result (or its absence)."""
    global _compress_fn
    if _compress_fn is not False:
        return _compress_fn  # type: ignore[return-value]
    try:
        from headroom import compress  # type: ignore

        _compress_fn = compress
    except Exception as exc:  # ImportError, or heavy optional-dep failure
        logger.info("Headroom not available; context compression disabled (%s)", exc)
        _compress_fn = None
    return _compress_fn  # type: ignore[return-value]


@dataclass
class CompressionStats:
    """Token accounting for a single compression call."""

    tokens_before: int
    tokens_after: int
    tokens_saved: int
    ratio: float
    transforms: list[str] = field(default_factory=list)


def compress_text(
    text: str, *, model: str | None = None
) -> tuple[str, CompressionStats | None]:
    """Compress one blob of assembled agent context. Always fail-safe.

    Returns ``(compressed_text, stats)`` on a real saving, otherwise
    ``(original_text, None)`` — when disabled, the dependency is missing, the
    blob is below the minimum size, savings are non-positive, or anything
    raises. The caller can use the returned text unconditionally.
    """
    if not text or not is_enabled():
        return text, None
    if len(text) < _min_chars():
        return text, None
    compress = _load_compress()
    if compress is None:
        return text, None
    try:
        result = compress(
            [{"role": "tool", "content": text}],
            model=_model_for_counting(model),
            # Single assembled blob: do not let the recency window protect the
            # only message away, but keep code intact under analyze/review.
            protect_recent=0,
            protect_analysis_context=True,
        )
        messages = getattr(result, "messages", None) or []
        if not messages:
            return text, None
        out = messages[0].get("content", text) if isinstance(messages[0], dict) else text
        if not isinstance(out, str) or not out:
            return text, None
        saved = int(getattr(result, "tokens_saved", 0) or 0)
        if saved <= 0:
            return text, None
        stats = CompressionStats(
            tokens_before=int(getattr(result, "tokens_before", 0) or 0),
            tokens_after=int(getattr(result, "tokens_after", 0) or 0),
            tokens_saved=saved,
            ratio=float(getattr(result, "compression_ratio", 0.0) or 0.0),
            transforms=list(getattr(result, "transforms_applied", []) or []),
        )
        return out, stats
    except Exception:
        logger.warning("Headroom compression failed; using original text", exc_info=True)
        return text, None


async def compress_text_async(
    text: str, *, model: str | None = None
) -> tuple[str, CompressionStats | None]:
    """Async wrapper that offloads the CPU-bound compress to a worker thread.

    Skips the thread hop entirely when compression is disabled, so the common
    (default-off) path adds no overhead.
    """
    if not text or not is_enabled():
        return text, None
    return await asyncio.to_thread(compress_text, text, model=model)
