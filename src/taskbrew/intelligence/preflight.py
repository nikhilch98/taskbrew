"""Pre-flight checks before task execution."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PreflightChecker:
    """Run pre-flight checks before agent task execution."""

    def __init__(self, db, cost_manager=None) -> None:
        self._db = db
        self._cost_manager = cost_manager

    async def run_checks(self, task: dict, role: str) -> dict:
        """Run all pre-flight checks. Returns dict with passed bool and check details."""
        checks = []
        all_passed = True

        # Check 1: Budget check
        if self._cost_manager:
            budget = await self._cost_manager.check_budget(role=role)
            budget_ok = budget.get("allowed", True)
            checks.append({
                "name": "budget",
                "passed": budget_ok,
                "details": f"Budget remaining: ${budget.get('remaining', 'unlimited')}" if budget_ok else f"Budget exceeded for scope: {budget.get('scope')}",
            })
            if not budget_ok:
                all_passed = False

        # Check 2: Task has required fields
        has_description = bool(task.get("description"))
        checks.append({
            "name": "task_completeness",
            "passed": has_description,
            "details": "Task has description" if has_description else "Task missing description",
        })
        # Don't fail on missing description, just warn

        # Check 3: No circular dependencies
        deps = await self._db.execute_fetchall(
            "SELECT * FROM task_dependencies WHERE task_id = ? AND resolved = 0",
            (task["id"],),
        )
        no_unresolved = len(deps) == 0
        checks.append({
            "name": "dependencies_resolved",
            "passed": no_unresolved,
            "details": "All dependencies resolved" if no_unresolved else f"{len(deps)} unresolved dependencies",
        })
        if not no_unresolved:
            all_passed = False

        return {"passed": all_passed, "checks": checks}
