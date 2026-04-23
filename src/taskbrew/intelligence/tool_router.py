"""Dynamic tool selection based on task context.

Audit 09 F#1 note: this module historically exposed only ``select_tools``
which returned a *recommended* tool list. Role-level tool allowlists
declared in YAML (``role.tools``) were advisory — there was no programmatic
``is_tool_allowed`` entrypoint, so no code path could actually refuse a
tool call. ``is_tool_allowed`` is now the single source of truth for
allowlist checks; the MCP tool dispatchers consult it via an environment
variable contract when the parent orchestrator spawns them.
"""

from __future__ import annotations

import logging
from typing import Iterable

logger = logging.getLogger(__name__)

# Tool profiles mapping task_type -> recommended tools
TOOL_PROFILES: dict[str, list[str]] = {
    "implementation": ["read_file", "write_file", "run_tests", "search_code", "git_commit"],
    "code_review": ["read_file", "search_code", "run_tests"],
    "testing": ["read_file", "write_file", "run_tests", "search_code"],
    "documentation": ["read_file", "write_file", "search_code"],
    "bug_fix": ["read_file", "write_file", "run_tests", "search_code", "git_diff", "git_commit"],
    "refactoring": ["read_file", "write_file", "run_tests", "search_code", "git_commit"],
    "architecture": ["read_file", "search_code"],
    "planning": ["read_file", "search_code"],
}

# Role-specific tool additions
ROLE_TOOLS: dict[str, list[str]] = {
    "coder": ["write_file", "git_commit", "run_tests"],
    "reviewer": ["read_file", "search_code", "run_tests"],
    "tester": ["write_file", "run_tests", "search_code"],
    "architect": ["read_file", "search_code"],
    "pm": ["read_file"],
}


class ToolRouter:
    """Select tools dynamically based on task type, role, and complexity."""

    def __init__(self, db=None) -> None:
        self._db = db

    async def select_tools(
        self, task_type: str | None = None, role: str | None = None, complexity: str = "medium"
    ) -> list[str]:
        """Return the recommended tool list for the given context.

        Merges task-type tools with role-specific tools, deduplicates, and
        optionally adds advanced tools for high-complexity tasks.
        """
        tools: set[str] = set()

        if task_type and task_type in TOOL_PROFILES:
            tools.update(TOOL_PROFILES[task_type])

        if role and role in ROLE_TOOLS:
            tools.update(ROLE_TOOLS[role])

        # If neither matched, return a default set
        if not tools:
            tools = {"read_file", "write_file", "search_code"}

        # High complexity gets extra tools
        if complexity == "high":
            tools.update(["git_diff", "git_log", "run_tests"])

        # Check for custom rules in DB
        if self._db:
            try:
                custom_rules = await self._db.execute_fetchall(
                    "SELECT * FROM model_routing_rules WHERE role = ? AND active = 1",
                    (role or "",),
                )
                for rule in custom_rules:
                    if rule.get("criteria"):
                        import json
                        criteria = json.loads(rule["criteria"])
                        if "extra_tools" in criteria:
                            tools.update(criteria["extra_tools"])
            except Exception:
                pass

        return sorted(tools)

    def get_profile(self, task_type: str) -> list[str]:
        """Return the tool profile for a task type (sync, no DB)."""
        return TOOL_PROFILES.get(task_type, [])

    def get_role_tools(self, role: str) -> list[str]:
        """Return the tools specific to a role (sync, no DB)."""
        return ROLE_TOOLS.get(role, [])

    @staticmethod
    def is_tool_allowed(
        allowed_tools: Iterable[str] | None,
        tool_name: str,
        *,
        open_set_on_empty: bool = True,
    ) -> bool:
        """Return True iff *tool_name* is permitted by *allowed_tools*.

        Parameters
        ----------
        allowed_tools:
            The role's declared tool allowlist (role.tools from YAML, or a
            pre-fetched set). ``None`` or empty iterable means "no allowlist
            was configured."
        tool_name:
            The tool being invoked (e.g. ``"create_task"``, ``"Bash"``).
        open_set_on_empty:
            When True (default) and no allowlist was configured, permit every
            tool -- preserves legacy behavior for un-configured roles.
            Set False in policy-enforcement contexts to deny-by-default.

        This is intentionally a pure function so callers can consult it from
        any context (MCP dispatchers, subprocess spawn paths, dashboard)
        without routing through a database.
        """
        if not tool_name or not isinstance(tool_name, str):
            return False
        if allowed_tools is None:
            return bool(open_set_on_empty)
        # Normalise to a set of non-empty strings.
        normalized = {t for t in allowed_tools if isinstance(t, str) and t}
        if not normalized:
            return bool(open_set_on_empty)
        return tool_name in normalized
