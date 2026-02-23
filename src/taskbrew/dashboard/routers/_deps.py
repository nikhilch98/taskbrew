"""Shared dependency: orchestrator reference set by app.py during create_app()."""

from __future__ import annotations

from fastapi import HTTPException


_orchestrator = None


def set_orchestrator(orch):
    """Called by app.py to inject the orchestrator (or fake) reference."""
    global _orchestrator
    _orchestrator = orch


def get_orch():
    """Return the current orchestrator or raise 409 if unavailable."""
    if _orchestrator is None:
        raise HTTPException(409, "No active project. Create or activate a project first.")
    return _orchestrator


def get_orch_optional():
    """Return the orchestrator or None (no error)."""
    return _orchestrator
