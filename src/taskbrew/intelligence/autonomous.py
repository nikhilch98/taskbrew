"""Autonomous decision making: task decomposition, work discovery, priority negotiation, retry strategies, self-healing."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class AutonomousManager:
    """Manage autonomous agent decision-making capabilities."""

    # Bid score weights: workload, skill, urgency
    BID_WEIGHT_WORKLOAD: float = 0.3
    BID_WEIGHT_SKILL: float = 0.4
    BID_WEIGHT_URGENCY: float = 0.3

    # Work discovery thresholds
    STALE_DOC_AGE_DAYS: int = 90

    def __init__(
        self,
        db,
        task_board=None,
        memory_manager=None,
        *,
        bid_weight_workload: float | None = None,
        bid_weight_skill: float | None = None,
        bid_weight_urgency: float | None = None,
        stale_doc_age_days: int | None = None,
    ) -> None:
        self._db = db
        self._task_board = task_board
        self._memory_manager = memory_manager
        if bid_weight_workload is not None:
            self.BID_WEIGHT_WORKLOAD = bid_weight_workload
        if bid_weight_skill is not None:
            self.BID_WEIGHT_SKILL = bid_weight_skill
        if bid_weight_urgency is not None:
            self.BID_WEIGHT_URGENCY = bid_weight_urgency
        if stale_doc_age_days is not None:
            self.STALE_DOC_AGE_DAYS = stale_doc_age_days

    # --- Feature 1: LLM-Powered Task Decomposition ---

    async def decompose_with_reasoning(
        self, task_id: str, llm_output: str | None = None
    ) -> dict:
        """Decompose a task into subtasks using LLM output or heuristic analysis.

        If *llm_output* is provided, parse it for numbered lists or bullet points
        to extract subtasks.  Otherwise, fall back to keyword-based heuristic
        decomposition of the task description.
        """
        task = await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )
        if not task:
            return {"task_id": task_id, "subtasks": [], "count": 0}

        now = datetime.now(timezone.utc).isoformat()
        subtasks: list[dict] = []

        if llm_output:
            # Parse numbered lists: "1. ...", "2) ...", "- ...", "* ..."
            lines = llm_output.strip().splitlines()
            for line in lines:
                line = line.strip()
                m = re.match(
                    r"^(?:\d+[\.\)]\s*|[-*]\s+)(.+)$", line
                )
                if m:
                    title = m.group(1).strip()
                    if title:
                        subtasks.append(
                            {
                                "subtask_title": title,
                                "subtask_description": title,
                                "reasoning": "Extracted from LLM output",
                                "estimated_effort": "medium",
                            }
                        )
        else:
            # Heuristic decomposition based on description keywords
            description = task.get("description") or task.get("title") or ""
            # Split on " and " to find parallel tasks
            parts: list[str] = []
            if " and " in description.lower():
                parts = [
                    p.strip()
                    for p in re.split(r"\s+and\s+", description, flags=re.IGNORECASE)
                    if p.strip()
                ]
            elif " then " in description.lower():
                parts = [
                    p.strip()
                    for p in re.split(r"\s+then\s+", description, flags=re.IGNORECASE)
                    if p.strip()
                ]

            for part in parts:
                subtasks.append(
                    {
                        "subtask_title": part[:120],
                        "subtask_description": part,
                        "reasoning": "Heuristic split from description",
                        "estimated_effort": "medium",
                    }
                )

        # Persist subtasks
        for st in subtasks:
            st_id = f"SUB-{uuid.uuid4().hex[:6]}"
            st["id"] = st_id
            st["task_id"] = task_id
            st["status"] = "pending"
            st["created_at"] = now
            await self._db.execute(
                "INSERT INTO task_decompositions "
                "(id, task_id, subtask_title, subtask_description, reasoning, "
                "estimated_effort, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    st_id,
                    task_id,
                    st["subtask_title"],
                    st["subtask_description"],
                    st["reasoning"],
                    st["estimated_effort"],
                    st["status"],
                    now,
                ),
            )

        return {"task_id": task_id, "subtasks": subtasks, "count": len(subtasks)}

    # --- Feature 2: Autonomous Work Discovery ---

    async def discover_work(
        self, agent_id: str, project_dir: str
    ) -> list[dict]:
        """Scan *project_dir* for actionable work items.

        Looks for:
        - Files containing ``TODO`` or ``FIXME`` comments
        - Python files without corresponding test files
        - Markdown files not modified in 90+ days
        """
        now = datetime.now(timezone.utc).isoformat()
        discoveries: list[dict] = []
        project = Path(project_dir)

        if not project.is_dir():
            return discoveries

        # Scan for TODO / FIXME
        for py_file in project.rglob("*.py"):
            try:
                content = py_file.read_text(errors="replace")
            except OSError as exc:
                logger.warning("Cannot read %s: %s", py_file, exc)
                continue
            for i, line in enumerate(content.splitlines(), 1):
                if re.search(r"\b(TODO|FIXME)\b", line):
                    discoveries.append(
                        {
                            "discovery_type": "todo_comment",
                            "file_path": str(py_file),
                            "description": f"Line {i}: {line.strip()[:200]}",
                            "priority": "medium",
                        }
                    )

        # Python files without tests
        src_files = list(project.rglob("*.py"))
        test_names = {f.name for f in src_files if f.name.startswith("test_")}
        for src in src_files:
            if src.name.startswith("test_") or src.name == "__init__.py":
                continue
            expected_test = f"test_{src.name}"
            if expected_test not in test_names:
                discoveries.append(
                    {
                        "discovery_type": "missing_test",
                        "file_path": str(src),
                        "description": f"No test file found (expected {expected_test})",
                        "priority": "high",
                    }
                )

        # Stale markdown files (>90 days)
        now_ts = datetime.now(timezone.utc).timestamp()
        for md_file in project.rglob("*.md"):
            try:
                mtime = md_file.stat().st_mtime
            except OSError as exc:
                logger.warning("Cannot stat %s: %s", md_file, exc)
                continue
            age_days = (now_ts - mtime) / 86400
            if age_days > self.STALE_DOC_AGE_DAYS:
                discoveries.append(
                    {
                        "discovery_type": "stale_doc",
                        "file_path": str(md_file),
                        "description": f"Markdown file not modified in {int(age_days)} days",
                        "priority": "low",
                    }
                )

        # Persist discoveries
        for d in discoveries:
            d_id = f"WD-{uuid.uuid4().hex[:6]}"
            d["id"] = d_id
            d["agent_id"] = agent_id
            d["status"] = "pending"
            d["created_at"] = now
            await self._db.execute(
                "INSERT INTO work_discoveries "
                "(id, agent_id, discovery_type, file_path, description, priority, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d_id,
                    agent_id,
                    d["discovery_type"],
                    d["file_path"],
                    d["description"],
                    d["priority"],
                    d["status"],
                    now,
                ),
            )

        return discoveries

    async def get_discoveries(
        self, status: str = "pending", limit: int = 50
    ) -> list[dict]:
        """Return work discoveries filtered by *status*."""
        return await self._db.execute_fetchall(
            "SELECT * FROM work_discoveries WHERE status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )

    # --- Feature 3: Priority Negotiation ---

    async def submit_bid(
        self,
        task_id: str,
        agent_id: str,
        workload: float,
        skill: float,
        urgency: float,
    ) -> dict:
        """Submit a priority bid for *task_id*.

        ``bid_score = W_workload * (1 - workload) + W_skill * skill + W_urgency * urgency``

        Weights are configurable via ``BID_WEIGHT_WORKLOAD``, ``BID_WEIGHT_SKILL``,
        and ``BID_WEIGHT_URGENCY`` class attributes or constructor parameters.
        """
        # Clamp input values to valid [0.0, 1.0] range
        workload = max(0.0, min(1.0, workload))
        skill = max(0.0, min(1.0, skill))
        urgency = max(0.0, min(1.0, urgency))
        bid_score = round(
            self.BID_WEIGHT_WORKLOAD * (1 - workload)
            + self.BID_WEIGHT_SKILL * skill
            + self.BID_WEIGHT_URGENCY * urgency,
            4,
        )
        now = datetime.now(timezone.utc).isoformat()
        bid_id = f"BID-{uuid.uuid4().hex[:6]}"
        reasoning = (
            f"workload={workload}, skill={skill}, urgency={urgency}"
        )

        await self._db.execute(
            "INSERT INTO priority_bids "
            "(id, task_id, agent_id, bid_score, reasoning, "
            "workload_factor, skill_factor, urgency_factor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                bid_id,
                task_id,
                agent_id,
                bid_score,
                reasoning,
                workload,
                skill,
                urgency,
                now,
            ),
        )

        return {
            "id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_score": bid_score,
            "reasoning": reasoning,
            "workload_factor": workload,
            "skill_factor": skill,
            "urgency_factor": urgency,
            "created_at": now,
        }

    async def resolve_bids(self, task_id: str) -> dict:
        """Resolve bids for *task_id* by selecting the highest scorer."""
        bids = await self._db.execute_fetchall(
            "SELECT * FROM priority_bids WHERE task_id = ? ORDER BY bid_score DESC",
            (task_id,),
        )
        if not bids:
            return {"task_id": task_id, "winner": None, "bid_score": 0, "total_bids": 0}

        winner = bids[0]
        return {
            "task_id": task_id,
            "winner": winner["agent_id"],
            "bid_score": winner["bid_score"],
            "total_bids": len(bids),
        }

    # --- Feature 4: Adaptive Retry Strategies ---

    async def record_retry_outcome(
        self,
        failure_type: str,
        strategy: str,
        success: bool,
        recovery_time_ms: int = 0,
    ) -> dict:
        """Record the outcome of a retry attempt.

        Uses INSERT OR REPLACE with a UNIQUE constraint on
        ``(failure_type, strategy)`` to maintain running averages.
        """
        existing = await self._db.execute_fetchone(
            "SELECT * FROM retry_strategies WHERE failure_type = ? AND strategy = ?",
            (failure_type, strategy),
        )

        now = datetime.now(timezone.utc).isoformat()

        if existing:
            new_success = existing["success_count"] + (1 if success else 0)
            new_failure = existing["failure_count"] + (0 if success else 1)
            total = existing["success_count"] + existing["failure_count"] + 1
            old_avg = existing["avg_recovery_time_ms"] or 0
            new_avg = round(
                (old_avg * (total - 1) + recovery_time_ms) / total
            )
            await self._db.execute(
                "UPDATE retry_strategies SET success_count = ?, failure_count = ?, "
                "avg_recovery_time_ms = ?, last_updated = ? "
                "WHERE id = ?",
                (new_success, new_failure, new_avg, now, existing["id"]),
            )
            return {
                "id": existing["id"],
                "failure_type": failure_type,
                "strategy": strategy,
                "success_count": new_success,
                "failure_count": new_failure,
                "avg_recovery_time_ms": new_avg,
                "last_updated": now,
            }
        else:
            rec_id = f"RS-{uuid.uuid4().hex[:6]}"
            s_count = 1 if success else 0
            f_count = 0 if success else 1
            await self._db.execute(
                "INSERT INTO retry_strategies "
                "(id, failure_type, strategy, success_count, failure_count, "
                "avg_recovery_time_ms, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rec_id, failure_type, strategy, s_count, f_count, recovery_time_ms, now),
            )
            return {
                "id": rec_id,
                "failure_type": failure_type,
                "strategy": strategy,
                "success_count": s_count,
                "failure_count": f_count,
                "avg_recovery_time_ms": recovery_time_ms,
                "last_updated": now,
            }

    async def get_best_retry_strategy(self, failure_type: str) -> dict | None:
        """Return the most effective retry strategy for *failure_type*."""
        row = await self._db.execute_fetchone(
            "SELECT *, "
            "CAST(success_count AS REAL) / (success_count + failure_count) AS success_rate "
            "FROM retry_strategies WHERE failure_type = ? "
            "ORDER BY success_rate DESC LIMIT 1",
            (failure_type,),
        )
        return row

    # --- Feature 5: Self-Healing Pipelines ---

    async def find_similar_fix(self, failure_signature: str) -> dict | None:
        """Find a previous fix matching *failure_signature* (LIKE search)."""
        return await self._db.execute_fetchone(
            "SELECT * FROM pipeline_fixes "
            "WHERE failure_signature LIKE ? "
            "ORDER BY success DESC LIMIT 1",
            (f"%{failure_signature}%",),
        )

    async def record_fix(
        self,
        failure_signature: str,
        fix_applied: str,
        success: bool,
        source_task_id: str | None = None,
    ) -> dict:
        """Record a pipeline fix attempt."""
        fix_id = f"FIX-{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO pipeline_fixes "
            "(id, failure_signature, fix_applied, success, source_task_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fix_id, failure_signature, fix_applied, int(success), source_task_id, now),
        )
        return {
            "id": fix_id,
            "failure_signature": failure_signature,
            "fix_applied": fix_applied,
            "success": success,
            "source_task_id": source_task_id,
            "created_at": now,
        }
