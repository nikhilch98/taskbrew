"""Shared orchestrator registry for the dashboard routers.

Historically this module held a single global ``_orchestrator`` reference set
by ``app.py`` during ``create_app()``. To support running several isolated
projects concurrently ("focus one, run many"), it now holds a *pool* of
orchestrators keyed by ``project_id`` plus a request-scoped
:class:`contextvars.ContextVar`.

Resolution order in :func:`_resolve` (used by ``get_orch``/``get_orch_optional``):

1. **Request-scoped project** — set by the ``_project_scope`` middleware from
   the ``X-Taskbrew-Project`` header. Agent MCP callbacks carry this header so
   their writes land on *their own* project's orchestrator, never the focused
   one. Resolution is **strict**: a header naming an unknown project returns
   ``None`` (→ 409) rather than silently falling back to the focused project,
   so a stale/wrong header can never cross-write another project's database.
2. **Focused project** — the one the dashboard is currently viewing. Used by
   all the human-facing view endpoints (no header present).
3. **Lone-orchestrator fallback** — when exactly one orchestrator is
   registered (the common single-project case and virtually every test), return
   it. Preserves backward compatibility with the old single-global behavior.
"""

from __future__ import annotations

import contextvars

from fastapi import HTTPException

# project_id -> orchestrator (or fake orch in tests)
_orchestrators: dict[str, object] = {}
_focused_id: str | None = None

# Request-scoped project id, set by the _project_scope middleware from the
# X-Taskbrew-Project header. Defaults to None (→ focused resolution).
_current_project: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "taskbrew_current_project", default=None
)


def _pid_of(orch) -> str:
    """Return a stable string key for *orch*.

    Real orchestrators carry a string ``project_id``. Test doubles (MagicMock,
    _FakeOrch) may not — fall back to a sentinel so the pool still works.
    """
    pid = getattr(orch, "project_id", None)
    return pid if isinstance(pid, str) and pid else "_default"


def register_orchestrator(orch, *, focus: bool = True) -> str:
    """Add *orch* to the pool. Focus it when *focus* is True (or none yet)."""
    global _focused_id
    pid = _pid_of(orch)
    _orchestrators[pid] = orch
    if focus or _focused_id is None:
        _focused_id = pid
    return pid


def unregister_orchestrator(pid_or_orch) -> None:
    """Remove an orchestrator from the pool (by id or instance).

    If it was the focused project, re-focus an arbitrary remaining one (or
    ``None`` when the pool is empty).
    """
    global _focused_id
    pid = pid_or_orch if isinstance(pid_or_orch, str) else _pid_of(pid_or_orch)
    _orchestrators.pop(pid, None)
    if _focused_id == pid:
        _focused_id = next(iter(_orchestrators), None)


def set_focused_project(pid: str | None) -> None:
    """Focus *pid* (must already be registered) or clear focus with None."""
    global _focused_id
    if pid is None or pid in _orchestrators:
        _focused_id = pid


def get_focused_project() -> str | None:
    """Return the currently focused project id (or None)."""
    return _focused_id


def get_all_orchestrators() -> dict[str, object]:
    """Return a shallow copy of the pool (project_id -> orchestrator)."""
    return dict(_orchestrators)


def get_orchestrator_for(pid: str):
    """Return the orchestrator registered under *pid* (or None)."""
    return _orchestrators.get(pid)


# ----------------------------------------------------------------------
# Request-scoped project (set by middleware from X-Taskbrew-Project header)
# ----------------------------------------------------------------------

def set_current_project(pid: str | None):
    """Bind the request-scoped project id; returns a reset token."""
    return _current_project.set(pid)


def reset_current_project(token) -> None:
    """Restore the request-scoped project id from a token (best-effort)."""
    try:
        _current_project.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. BaseHTTPMiddleware task hop).
        pass


def get_current_project() -> str | None:
    """Return the request-scoped project id (or None)."""
    return _current_project.get()


# ----------------------------------------------------------------------
# Resolution
# ----------------------------------------------------------------------

# Optional sync ``pid -> orch|None`` lookup for read-only "idle views" of
# registered-but-not-running projects, wired by app.py
# (-> ProjectManager.get_readonly_cached). Lets a scoped request for a stopped
# project resolve to a view backed by that project's OWN database, so its board
# and detail pages render without starting it. It always returns the project
# named in the header (or None for unregistered ids), so the strict
# cross-project-write guarantee is preserved.
_readonly_getter = None


def set_readonly_getter(fn):
    """Inject the read-only idle-view lookup (``pid -> orch|None``)."""
    global _readonly_getter
    _readonly_getter = fn


def _resolve():
    """Resolve the orchestrator for the current request. See module docstring."""
    pid = _current_project.get()
    if pid:
        # Strict: an explicit header binds to *that* project only. Prefer the
        # live running orchestrator; fall back to a read-only idle view of the
        # same (registered) project when it isn't running. An unregistered id
        # resolves to None (→ 409 for humans / 503 for agent writes) rather than
        # cross-writing a different project's database.
        orch = _orchestrators.get(pid)
        if orch is not None:
            return orch
        if _readonly_getter is not None:
            return _readonly_getter(pid)
        return None
    if _focused_id is not None:
        orch = _orchestrators.get(_focused_id)
        if orch is not None:
            return orch
    if len(_orchestrators) == 1:
        return next(iter(_orchestrators.values()))
    return None


def set_orchestrator(orch):
    """Back-compat single-orchestrator setter used by app.py and system.py.

    ``set_orchestrator(orch)`` registers *orch* and focuses it.
    ``set_orchestrator(None)`` drops the focused orchestrator from the pool and
    re-focuses a remaining one (or None). This preserves the old
    deactivate-clears-everything behavior for single-project tests while
    leaving any other concurrently-running projects untouched.
    """
    global _focused_id
    if orch is None:
        if _focused_id is not None:
            _orchestrators.pop(_focused_id, None)
        _focused_id = next(iter(_orchestrators), None)
        return
    register_orchestrator(orch, focus=True)


def get_orch():
    """Return the resolved orchestrator or raise 409 if unavailable."""
    orch = _resolve()
    if orch is None:
        raise HTTPException(409, "No active project. Create or activate a project first.")
    return orch


def get_orch_optional():
    """Return the resolved orchestrator or None (no error)."""
    return _resolve()
