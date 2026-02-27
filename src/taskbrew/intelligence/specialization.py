"""Agent specialization, skill tracking, and model routing."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SpecializationManager:
    """Track agent skills, route models, and adapt prompts.

    Features:
    - Skill badges with weighted moving average proficiency
    - Model routing based on role + complexity
    - Prompt tuning from stored memories
    - Role gap detection from rejection patterns
    """

    def __init__(self, db) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Skill Badges
    # ------------------------------------------------------------------

    async def update_skill_badge(self, agent_role: str, skill_type: str, success: bool) -> dict:
        """Update a skill badge after task completion.

        Uses weighted moving average: new = 0.9 * old + 0.1 * (1.0 if success else 0.0)
        """
        now = datetime.now(timezone.utc).isoformat()
        score = 1.0 if success else 0.0

        existing = await self._db.execute_fetchone(
            "SELECT * FROM skill_badges WHERE agent_role = ? AND skill_type = ?",
            (agent_role, skill_type),
        )

        if existing:
            new_proficiency = 0.9 * existing["proficiency"] + 0.1 * score
            new_completed = existing["tasks_completed"] + 1
            new_success_rate = (
                (existing["success_rate"] * existing["tasks_completed"] + score)
                / new_completed
            )
            await self._db.execute(
                "UPDATE skill_badges SET proficiency = ?, tasks_completed = ?, "
                "success_rate = ?, last_updated = ? WHERE id = ?",
                (new_proficiency, new_completed, new_success_rate, now, existing["id"]),
            )
            return {
                "agent_role": agent_role,
                "skill_type": skill_type,
                "proficiency": new_proficiency,
                "tasks_completed": new_completed,
                "success_rate": new_success_rate,
            }
        else:
            await self._db.execute(
                "INSERT INTO skill_badges (agent_role, skill_type, proficiency, tasks_completed, "
                "success_rate, last_updated) VALUES (?, ?, ?, 1, ?, ?)",
                (agent_role, skill_type, score, score, now),
            )
            return {
                "agent_role": agent_role,
                "skill_type": skill_type,
                "proficiency": score,
                "tasks_completed": 1,
                "success_rate": score,
            }

    async def get_agent_skills(self, agent_role: str) -> list[dict]:
        """Get all skill badges for an agent role."""
        return await self._db.execute_fetchall(
            "SELECT * FROM skill_badges WHERE agent_role = ? ORDER BY proficiency DESC",
            (agent_role,),
        )

    async def get_best_agent_for_task(self, skill_type: str) -> dict | None:
        """Find the agent role with the highest proficiency for a skill."""
        return await self._db.execute_fetchone(
            "SELECT * FROM skill_badges WHERE skill_type = ? ORDER BY proficiency DESC LIMIT 1",
            (skill_type,),
        )

    # ------------------------------------------------------------------
    # Model Routing
    # ------------------------------------------------------------------

    async def route_model(self, role: str, complexity: str = "medium") -> str | None:
        """Determine which model to use based on role and complexity.

        Returns the model name or None if no rule matches (use default).
        """
        rule = await self._db.execute_fetchone(
            "SELECT model FROM model_routing_rules "
            "WHERE role = ? AND complexity_threshold = ? AND active = 1 "
            "ORDER BY id DESC LIMIT 1",
            (role, complexity),
        )
        return rule["model"] if rule else None

    async def set_routing_rule(self, role: str, complexity: str, model: str,
                               criteria: dict | None = None) -> int:
        """Create or update a model routing rule."""
        now = datetime.now(timezone.utc).isoformat()
        criteria_json = json.dumps(criteria) if criteria else None

        # Deactivate existing rules for same role+complexity
        await self._db.execute(
            "UPDATE model_routing_rules SET active = 0 "
            "WHERE role = ? AND complexity_threshold = ? AND active = 1",
            (role, complexity),
        )

        rows = await self._db.execute_returning(
            "INSERT INTO model_routing_rules (role, complexity_threshold, model, criteria, active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?) RETURNING id",
            (role, complexity, model, criteria_json, now),
        )
        return rows[0]["id"]

    async def get_routing_rules(self, role: str | None = None) -> list[dict]:
        """Get active routing rules, optionally filtered by role."""
        if role:
            return await self._db.execute_fetchall(
                "SELECT * FROM model_routing_rules WHERE role = ? AND active = 1 ORDER BY complexity_threshold",
                (role,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM model_routing_rules WHERE active = 1 ORDER BY role, complexity_threshold"
        )

    # ------------------------------------------------------------------
    # Prompt Tuning
    # ------------------------------------------------------------------

    async def get_prompt_tunings(self, agent_role: str) -> list[dict]:
        """Get stored prompt tuning memories for an agent role.

        Prompt tunings are stored as memory_type='prompt_tuning' in agent_memories.
        """
        return await self._db.execute_fetchall(
            "SELECT title, content FROM agent_memories "
            "WHERE agent_role = ? AND memory_type = 'prompt_tuning' "
            "ORDER BY relevance_score DESC LIMIT 10",
            (agent_role,),
        )

    async def store_prompt_tuning(self, agent_role: str, title: str, content: str) -> int:
        """Store a prompt tuning rule for an agent role."""
        now = datetime.now(timezone.utc).isoformat()
        rows = await self._db.execute_returning(
            "INSERT INTO agent_memories (agent_role, memory_type, title, content, relevance_score, created_at) "
            "VALUES (?, 'prompt_tuning', ?, ?, 1.0, ?) RETURNING id",
            (agent_role, title, content, now),
        )
        return rows[0]["id"]

    # ------------------------------------------------------------------
    # Role Gap Detection
    # ------------------------------------------------------------------

    async def analyze_rejections(self, agent_role: str, lookback_limit: int = 50) -> dict:
        """Analyze task rejection patterns for an agent role.

        Looks at rejected/failed tasks to identify systematic weaknesses.
        """
        rejections = await self._db.execute_fetchall(
            "SELECT task_type, rejection_reason FROM tasks "
            "WHERE assigned_to = ? AND status IN ('rejected', 'failed') "
            "AND rejection_reason IS NOT NULL "
            "ORDER BY completed_at DESC LIMIT ?",
            (agent_role, lookback_limit),
        )

        if not rejections:
            return {"agent_role": agent_role, "rejections": 0, "patterns": [], "suggestions": []}

        # Count rejection reasons by category
        categories: dict[str, int] = {}
        for r in rejections:
            reason = (r.get("rejection_reason") or "").lower()
            if "test" in reason:
                categories["testing"] = categories.get("testing", 0) + 1
            elif "style" in reason or "format" in reason:
                categories["code_style"] = categories.get("code_style", 0) + 1
            elif "security" in reason:
                categories["security"] = categories.get("security", 0) + 1
            elif "scope" in reason or "requirement" in reason:
                categories["scope_adherence"] = categories.get("scope_adherence", 0) + 1
            else:
                categories["other"] = categories.get("other", 0) + 1

        sorted_patterns = sorted(categories.items(), key=lambda x: x[1], reverse=True)

        suggestions = []
        for pattern, count in sorted_patterns:
            if count >= 2:
                suggestions.append(f"Add prompt tuning for {pattern} (rejected {count} times)")

        return {
            "agent_role": agent_role,
            "rejections": len(rejections),
            "patterns": [{"category": k, "count": v} for k, v in sorted_patterns],
            "suggestions": suggestions,
        }

    async def detect_role_gaps(self) -> list[dict]:
        """Detect roles that have consistent failure patterns.

        Returns roles with high rejection rates that might need new sub-roles
        or prompt adjustments.
        """
        # Get roles with task counts
        role_stats = await self._db.execute_fetchall(
            "SELECT assigned_to as role, "
            "COUNT(*) as total_tasks, "
            "SUM(CASE WHEN status IN ('rejected', 'failed') THEN 1 ELSE 0 END) as failed_tasks "
            "FROM tasks WHERE assigned_to IS NOT NULL "
            "GROUP BY assigned_to"
        )

        gaps = []
        for stat in role_stats:
            total = stat["total_tasks"]
            failed = stat["failed_tasks"] or 0
            if total >= 3 and failed / total > 0.3:
                gaps.append({
                    "role": stat["role"],
                    "total_tasks": total,
                    "failed_tasks": failed,
                    "failure_rate": round(failed / total, 2),
                    "recommendation": "Consider prompt tuning or role specialization",
                })

        return sorted(gaps, key=lambda x: x["failure_rate"], reverse=True)
