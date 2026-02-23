"""Task decomposition, estimation, risk assessment, and rollback planning."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class PlanningManager:
    """Manage task planning: decomposition, estimation, risk, alternatives, and rollback."""

    def __init__(self, db, task_board=None) -> None:
        self._db = db
        self._task_board = task_board

    async def _store_plan(
        self, task_id: str, plan_type: str, content: dict, confidence: float | None = None, created_by: str | None = None
    ) -> dict:
        """Store a plan in the task_plans table."""
        now = datetime.now(timezone.utc).isoformat()
        content_json = json.dumps(content)
        await self._db.execute(
            "INSERT INTO task_plans (task_id, plan_type, content, confidence, created_by, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'draft', ?)",
            (task_id, plan_type, content_json, confidence, created_by, now),
        )
        return {"task_id": task_id, "plan_type": plan_type, "content": content, "confidence": confidence, "created_at": now}

    async def get_plans(self, task_id: str, plan_type: str | None = None) -> list[dict]:
        """Get plans for a task, optionally filtered by type."""
        if plan_type:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM task_plans WHERE task_id = ? AND plan_type = ? ORDER BY created_at DESC",
                (task_id, plan_type),
            )
        else:
            rows = await self._db.execute_fetchall(
                "SELECT * FROM task_plans WHERE task_id = ? ORDER BY created_at DESC",
                (task_id,),
            )
        # Parse JSON content
        for row in rows:
            if isinstance(row.get("content"), str):
                try:
                    row["content"] = json.loads(row["content"])
                except (json.JSONDecodeError, TypeError):
                    pass
        return rows

    async def decompose_task(self, task_id: str) -> dict:
        """Analyze a task and suggest decomposition into subtasks.

        Uses heuristics based on description length, keywords, and file references.
        """
        task = None
        if self._task_board:
            task = await self._task_board.get_task(task_id)
        if not task:
            task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        description = task.get("description") or ""
        title = task.get("title") or ""
        full_text = f"{title} {description}"

        # Heuristic decomposition
        subtasks = []

        # Look for numbered lists or bullet points
        lines = description.split("\n")
        for line in lines:
            stripped = line.strip()
            if re.match(r'^(\d+[\.\)]\s+|-\s+|\*\s+)', stripped):
                item = re.sub(r'^(\d+[\.\)]\s+|-\s+|\*\s+)', '', stripped).strip()
                if len(item) > 10:
                    subtasks.append({"title": item[:120], "type": "implementation"})

        # If no explicit list found, suggest based on keywords
        if not subtasks:
            if any(kw in full_text.lower() for kw in ["test", "spec", "verify"]):
                subtasks.append({"title": f"Write tests for: {title[:80]}", "type": "qa_verification"})
            if any(kw in full_text.lower() for kw in ["implement", "build", "create", "add"]):
                subtasks.append({"title": f"Implement: {title[:80]}", "type": "implementation"})
            if any(kw in full_text.lower() for kw in ["review", "check", "audit"]):
                subtasks.append({"title": f"Review: {title[:80]}", "type": "code_review"})
            if any(kw in full_text.lower() for kw in ["design", "architect", "plan"]):
                subtasks.append({"title": f"Design: {title[:80]}", "type": "tech_design"})

        complexity = "simple" if len(subtasks) <= 1 else "medium" if len(subtasks) <= 3 else "complex"
        plan = {
            "subtasks": subtasks,
            "complexity": complexity,
            "word_count": len(full_text.split()),
        }
        return await self._store_plan(task_id, "decomposition", plan, confidence=0.7)

    async def estimate_effort(self, task_id: str) -> dict:
        """Estimate effort for a task based on description and historical data."""
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        description = task.get("description") or ""
        task_type = task.get("task_type") or "general"

        # Historical averages from task_usage
        historical = await self._db.execute_fetchone(
            "SELECT AVG(cost_usd) as avg_cost, AVG(duration_api_ms) as avg_duration, "
            "AVG(num_turns) as avg_turns, COUNT(*) as sample_size "
            "FROM task_usage tu JOIN tasks t ON tu.task_id = t.id "
            "WHERE t.task_type = ?",
            (task_type,),
        )

        word_count = len(description.split())
        # File references as complexity indicator
        file_refs = len(re.findall(r'[\w/]+\.\w{1,5}', description))

        # Simple heuristic scoring
        if word_count < 50 and file_refs <= 1:
            complexity = "simple"
            tokens_estimate = 5000
            time_estimate_min = 2
        elif word_count < 200 and file_refs <= 5:
            complexity = "medium"
            tokens_estimate = 15000
            time_estimate_min = 10
        else:
            complexity = "complex"
            tokens_estimate = 40000
            time_estimate_min = 30

        estimate = {
            "complexity": complexity,
            "tokens_estimate": tokens_estimate,
            "time_estimate_min": time_estimate_min,
            "word_count": word_count,
            "file_references": file_refs,
            "historical": {
                "avg_cost": round(historical["avg_cost"], 4) if historical and historical["avg_cost"] else None,
                "avg_duration_ms": int(historical["avg_duration"]) if historical and historical["avg_duration"] else None,
                "avg_turns": round(historical["avg_turns"], 1) if historical and historical["avg_turns"] else None,
                "sample_size": historical["sample_size"] if historical else 0,
            },
        }
        return await self._store_plan(task_id, "estimate", estimate, confidence=0.6)

    async def assess_risk(self, task_id: str, files_to_change: list[str] | None = None) -> dict:
        """Assess risk for a task based on blast radius and dependencies."""
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        risk_factors = []
        risk_score = 0.0

        # Check dependencies
        deps = await self._db.execute_fetchall(
            "SELECT * FROM task_dependencies WHERE task_id = ? AND resolved = 0",
            (task_id,),
        )
        if deps:
            risk_factors.append(f"Has {len(deps)} unresolved dependencies")
            risk_score += 0.2 * len(deps)

        # Check file blast radius
        if files_to_change:
            risk_factors.append(f"Modifies {len(files_to_change)} files")
            risk_score += 0.1 * len(files_to_change)
            # Check for high-risk files
            high_risk = [f for f in files_to_change if any(p in f for p in ["__init__", "main", "config", "database", "migration"])]
            if high_risk:
                risk_factors.append(f"Modifies high-risk files: {', '.join(high_risk[:3])}")
                risk_score += 0.3

        # Priority-based risk
        if task.get("priority") == "critical":
            risk_factors.append("Critical priority task")
            risk_score += 0.2

        risk_level = "low" if risk_score < 0.3 else "medium" if risk_score < 0.6 else "high"
        mitigations = []
        if risk_level != "low":
            mitigations.append("Run full test suite before and after changes")
            mitigations.append("Create backup branch before modifications")
        if risk_score >= 0.6:
            mitigations.append("Request human review before merge")

        risk = {
            "risk_level": risk_level,
            "risk_score": min(1.0, risk_score),
            "risk_factors": risk_factors,
            "mitigations": mitigations,
            "files_to_change": files_to_change or [],
        }
        return await self._store_plan(task_id, "risk", risk, confidence=0.7)

    async def generate_alternatives(self, task_id: str) -> dict:
        """Generate alternative approaches for a task based on task type."""
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        task_type = task.get("task_type") or "general"

        # Generate contextually relevant alternatives based on task type
        if task_type == "implementation":
            approaches = [
                {"name": "Direct Implementation", "description": "Implement the feature directly as described", "risk": "low", "effort": "medium"},
                {"name": "Test-Driven (write tests first)", "description": "Write tests before implementation to define expected behavior", "risk": "low", "effort": "high"},
                {"name": "Spike/Prototype first", "description": "Build a quick prototype to validate approach, then implement properly", "risk": "medium", "effort": "low"},
            ]
            recommended = "Direct Implementation"
        elif task_type == "bug_fix":
            approaches = [
                {"name": "Hot fix (minimal change)", "description": "Apply the smallest possible change to fix the immediate issue", "risk": "low", "effort": "low"},
                {"name": "Root cause fix (deeper refactor)", "description": "Investigate and fix the underlying root cause with a deeper refactor", "risk": "medium", "effort": "high"},
                {"name": "Workaround + backlog cleanup ticket", "description": "Apply a temporary workaround and create a backlog ticket for proper fix", "risk": "low", "effort": "low"},
            ]
            recommended = "Root cause fix (deeper refactor)"
        elif task_type == "code_review":
            approaches = [
                {"name": "Automated lint + manual spot check", "description": "Run automated linting tools and manually spot-check critical sections", "risk": "low", "effort": "low"},
                {"name": "Full line-by-line review", "description": "Thoroughly review every line of changed code for correctness and style", "risk": "low", "effort": "high"},
                {"name": "Focus on test coverage gaps", "description": "Prioritize reviewing areas with insufficient test coverage", "risk": "low", "effort": "medium"},
            ]
            recommended = "Full line-by-line review"
        else:
            # Default fallback for unknown task types
            approaches = [
                {"name": "Direct Implementation", "description": "Implement the feature directly as described", "risk": "low", "effort": "medium"},
                {"name": "Incremental Approach", "description": "Break into smaller PRs, implement incrementally", "risk": "low", "effort": "high"},
                {"name": "Prototype First", "description": "Build a quick prototype, then refine", "risk": "medium", "effort": "low"},
            ]
            recommended = "Direct Implementation"

        alternatives = {
            "task_title": task.get("title", ""),
            "task_type": task_type,
            "approaches": approaches,
            "recommended": recommended,
        }
        return await self._store_plan(task_id, "alternatives", alternatives)

    async def create_rollback_plan(self, task_id: str) -> dict:
        """Create a rollback plan for a task."""
        task = await self._db.execute_fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            return {"error": "Task not found"}

        rollback = {
            "task_id": task_id,
            "steps": [
                "Revert the feature branch: git revert --no-commit HEAD",
                "Run test suite to verify rollback",
                "Deploy previous stable version",
            ],
            "verification": "Run full test suite after rollback",
            "communication": "Notify team of rollback and root cause",
        }
        return await self._store_plan(task_id, "rollback", rollback, confidence=0.8)
