"""Runtime tool-allowlist enforcement for MCP stdio servers.

Contract
--------
Parents spawn MCP tool-server subprocesses with two environment variables
that declare the role's policy:

* ``TASKBREW_ALLOWED_TOOLS``  — comma-separated tool names permitted for
  this role. When unset or empty, enforcement is treated as "open set"
  (legacy behavior preserved for roles that do not declare ``tools`` in
  YAML).
* ``TASKBREW_TOOL_ENFORCEMENT`` — one of ``"log"`` (default) or ``"deny"``.
  ``log`` emits a WARNING when a disallowed tool is invoked but still runs
  the tool. ``deny`` raises :class:`PermissionError` instead.

The dispatcher (``task_tools.py``, ``intelligence_tools.py``) calls
:func:`gate_tool_call` at the start of every MCP tool function. The
execution path short-circuits to a structured error message in ``deny``
mode so the LLM sees why the call was refused.

This closes the audit 09 F#1 gap at the dispatch boundary while keeping
the rollout staged: today’s default is ``log``. Flipping the default to
``deny`` is a one-line change once the parent orchestrator is updated to
always export ``TASKBREW_ALLOWED_TOOLS``.
"""

from __future__ import annotations

import logging
import os

from taskbrew.intelligence.tool_router import ToolRouter

logger = logging.getLogger(__name__)

_ALLOWED_ENV = "TASKBREW_ALLOWED_TOOLS"
_MODE_ENV = "TASKBREW_TOOL_ENFORCEMENT"
_ROLE_ENV = "TASKBREW_AGENT_ROLE"


def _parse_allowlist(raw: str | None) -> list[str] | None:
    """Return the allowlist parsed from env, or None when unset."""
    if raw is None:
        return None
    items = [t.strip() for t in raw.split(",") if t.strip()]
    return items


def _mode() -> str:
    value = os.environ.get(_MODE_ENV, "log").strip().lower()
    return "deny" if value == "deny" else "log"


def gate_tool_call(tool_name: str) -> tuple[bool, str | None]:
    """Check whether *tool_name* is permitted in the current environment.

    Returns ``(allowed, reason)``:

    * ``allowed`` is True when the caller may proceed (either because no
      allowlist is configured, or because the tool is listed, or because
      enforcement is in ``log`` mode and the tool is disallowed).
    * ``reason`` is a short human-readable message when the call is being
      denied in ``deny`` mode, otherwise None.

    Callers should call this at the top of every MCP tool function and,
    when ``allowed`` is False, return the reason string instead of doing
    the work.
    """
    allowed = _parse_allowlist(os.environ.get(_ALLOWED_ENV))
    role = os.environ.get(_ROLE_ENV) or "<unspecified>"

    if ToolRouter.is_tool_allowed(allowed, tool_name, open_set_on_empty=True):
        return True, None

    if _mode() == "deny":
        reason = (
            f"Tool {tool_name!r} is not in the allowlist for role {role!r}. "
            f"Permitted tools: {', '.join(allowed or []) or '<none declared>'}"
        )
        logger.warning(
            "tool-gate DENY: role=%s tool=%s allowed=%s",
            role, tool_name, allowed,
        )
        return False, reason

    # log mode: warn but allow the call through.
    logger.warning(
        "tool-gate LOG-ONLY: role=%s invoked non-allowlisted tool=%s (allowlist=%s); "
        "set TASKBREW_TOOL_ENFORCEMENT=deny to block.",
        role, tool_name, allowed,
    )
    return True, None


def gate_or_error(tool_name: str) -> str | None:
    """Convenience wrapper returning a denial string or None when permitted.

    Use this inside an MCP tool body::

        denial = gate_or_error("create_task")
        if denial:
            return denial

    so the deny-mode code path short-circuits without executing side
    effects.
    """
    allowed, reason = gate_tool_call(tool_name)
    if allowed:
        return None
    return f"Error: {reason}"


__all__ = ["gate_tool_call", "gate_or_error", "_parse_allowlist"]
