"""Dynamic tool selection based on task context."""

from __future__ import annotations

import logging

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
