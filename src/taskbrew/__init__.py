"""TaskBrew - multi-agent development automation."""

from __future__ import annotations

# audit 01 F#10 / 17 F#2 / 18 F#2: single source of truth for the
# package version is pyproject.toml. Read it via importlib.metadata so
# `taskbrew --version`, FastAPI's OpenAPI header, and any consumer of
# __version__ always agree with the installed distribution metadata.
try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
except ImportError:  # pragma: no cover
    # Python <3.8 - not supported but kept for safety.
    _pkg_version = None  # type: ignore[assignment]
    PackageNotFoundError = Exception  # type: ignore[assignment]


def _detect_version() -> str:
    if _pkg_version is None:
        return "0.0.0+unknown"
    try:
        return _pkg_version("taskbrew")
    except PackageNotFoundError:
        # Running from a source tree without the dist metadata (e.g. a
        # fresh clone before `pip install -e .`). Fall back to a
        # machine-readable sentinel rather than a lying constant.
        return "0.0.0+source"


__version__ = _detect_version()
