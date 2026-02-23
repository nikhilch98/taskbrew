"""Advanced planning: scheduling, resource planning, deadlines, scope creep, incremental delivery, post-mortems."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class AdvancedPlanningManager:
    """Advanced planning and scheduling for the AI team pipeline."""

    # Confidence interval multipliers for deadline estimation
    CONFIDENCE_LOW_MULTIPLIER: float = 0.8
    CONFIDENCE_HIGH_MULTIPLIER: float = 1.5

    # Scope creep detection: growth percentage threshold
    SCOPE_CREEP_GROWTH_THRESHOLD_PCT: float = 50.0

    def __init__(
        self,
        db,
        task_board=None,
        *,
        confidence_low_multiplier: float | None = None,
        confidence_high_multiplier: float | None = None,
        scope_creep_growth_threshold_pct: float | None = None,
    ) -> None:
        self._db = db
        self._task_board = task_board
        if confidence_low_multiplier is not None:
            self.CONFIDENCE_LOW_MULTIPLIER = confidence_low_multiplier
        if confidence_high_multiplier is not None:
            self.CONFIDENCE_HIGH_MULTIPLIER = confidence_high_multiplier
        if scope_creep_growth_threshold_pct is not None:
            self.SCOPE_CREEP_GROWTH_THRESHOLD_PCT = scope_creep_growth_threshold_pct

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    async def ensure_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS scheduling_graph (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                depends_on TEXT,
                scheduled_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resource_snapshots (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                active_tasks INTEGER NOT NULL DEFAULT 0,
                capacity TEXT NOT NULL DEFAULT 'available',
                snapshot_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deadline_estimates (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                estimated_hours REAL,
                confidence_low REAL,
                confidence_high REAL,
                based_on_samples INTEGER NOT NULL DEFAULT 0,
                estimated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scope_creep_flags (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                original_length INTEGER,
                current_length INTEGER,
                growth_pct REAL,
                flagged_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS delivery_increments (
                id TEXT PRIMARY KEY,
                feature_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                increment_order INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'planned',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS post_mortems (
                id TEXT PRIMARY KEY,
                task_id TEXT,
                group_id TEXT,
                total_tasks INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                success_rate REAL NOT NULL DEFAULT 0,
                avg_duration_hours REAL,
                common_failures TEXT,
                lessons TEXT,
                created_at TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------
    # Feature 45: Dependency-Aware Scheduling
    # ------------------------------------------------------------------

    async def build_schedule(self, group_id: str) -> list[dict]:
        """Build a dependency-aware schedule for all tasks in a group.

        Reads the ``depends_on`` field (comma-separated task IDs or NULL) from
        the tasks table, performs a topological sort, and persists the ordered
        schedule into the ``scheduling_graph`` table.
        """
        now = _utcnow()

        tasks = await self._db.execute_fetchall(
            "SELECT id, title, status, task_type, depends_on FROM tasks WHERE group_id = ?",
            (group_id,),
        )
        if not tasks:
            return []

        # Build lookup: task_id -> list of dependency task_ids
        task_ids = {t["id"] for t in tasks}
        dep_map: dict[str, list[str]] = {}
        for t in tasks:
            raw = t.get("depends_on") or ""
            deps = [d.strip() for d in raw.split(",") if d.strip() and d.strip() in task_ids]
            dep_map[t["id"]] = deps

        # Topological sort by levels:
        # Level 0 = no dependencies, level N = all deps satisfied at level < N
        assigned: dict[str, int] = {}
        remaining = set(task_ids)

        current_order = 0
        while remaining:
            # Find tasks whose dependencies are all already assigned
            ready = []
            for tid in remaining:
                if all(d in assigned for d in dep_map[tid]):
                    ready.append(tid)
            if not ready:
                # Cycle detected - assign remaining at current_order but flag it
                logger.warning(
                    "Dependency cycle detected involving tasks: %s",
                    list(remaining),
                )
                for tid in remaining:
                    assigned[tid] = current_order
                remaining.clear()
                break
            for tid in ready:
                assigned[tid] = current_order
                remaining.discard(tid)
            current_order += 1

        # Clear old schedule for this group
        await self._db.execute(
            "DELETE FROM scheduling_graph WHERE group_id = ?", (group_id,),
        )

        # Persist and build result
        schedule: list[dict] = []
        for t in sorted(tasks, key=lambda x: assigned.get(x["id"], 0)):
            tid = t["id"]
            sid = _new_id()
            deps_str = ",".join(dep_map[tid]) if dep_map[tid] else None
            order = assigned[tid]
            await self._db.execute(
                "INSERT INTO scheduling_graph (id, group_id, task_id, depends_on, scheduled_order, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, group_id, tid, deps_str, order, now),
            )
            schedule.append({
                "id": sid,
                "group_id": group_id,
                "task_id": tid,
                "depends_on": deps_str,
                "scheduled_order": order,
                "created_at": now,
            })

        return schedule

    async def get_schedule(self, group_id: str) -> list[dict]:
        """Return the ordered schedule for a group."""
        return await self._db.execute_fetchall(
            "SELECT * FROM scheduling_graph WHERE group_id = ? ORDER BY scheduled_order",
            (group_id,),
        )

    # ------------------------------------------------------------------
    # Feature 46: Resource-Aware Planning
    # ------------------------------------------------------------------

    async def snapshot_resources(self) -> list[dict]:
        """Count in-progress tasks per agent and store a resource snapshot."""
        now = _utcnow()

        rows = await self._db.execute_fetchall(
            "SELECT assigned_to AS agent_id, COUNT(*) AS active_tasks "
            "FROM tasks WHERE status = 'in_progress' AND assigned_to IS NOT NULL "
            "GROUP BY assigned_to",
        )

        snapshots: list[dict] = []
        for row in rows:
            agent_id = row["agent_id"]
            active = row["active_tasks"]
            capacity = "busy" if active > 0 else "available"
            sid = _new_id()
            await self._db.execute(
                "INSERT INTO resource_snapshots (id, agent_id, active_tasks, capacity, snapshot_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, agent_id, active, capacity, now),
            )
            snapshots.append({
                "id": sid,
                "agent_id": agent_id,
                "active_tasks": active,
                "capacity": capacity,
                "snapshot_at": now,
            })
        return snapshots

    async def plan_with_resources(self, group_id: str) -> list[dict]:
        """Assign pending tasks in a group to the least-busy agents."""
        # Snapshot current resources
        snapshots = await self.snapshot_resources()

        # Build load map from snapshots (agent_id -> active_tasks)
        load_map: dict[str, int] = {}
        for s in snapshots:
            load_map[s["agent_id"]] = s["active_tasks"]

        # If no agents found, look at all distinct assigned_to values
        if not load_map:
            agents = await self._db.execute_fetchall(
                "SELECT DISTINCT assigned_to FROM tasks WHERE assigned_to IS NOT NULL",
            )
            for a in agents:
                if a["assigned_to"] not in load_map:
                    load_map[a["assigned_to"]] = 0

        # Get pending tasks in the group
        pending = await self._db.execute_fetchall(
            "SELECT id, assigned_to FROM tasks WHERE group_id = ? AND status = 'pending'",
            (group_id,),
        )

        assignments: list[dict] = []
        for task in pending:
            if not load_map:
                break
            # Pick least-busy agent
            least_busy = min(load_map, key=load_map.get)
            load_map[least_busy] += 1
            assignments.append({
                "task_id": task["id"],
                "assigned_to": least_busy,
            })

        return assignments

    # ------------------------------------------------------------------
    # Feature 47: Deadline Estimation
    # ------------------------------------------------------------------

    async def estimate_deadline(self, task_id: str) -> dict:
        """Estimate deadline from historical avg completion times for same task_type.

        Confidence intervals use ``CONFIDENCE_LOW_MULTIPLIER`` and
        ``CONFIDENCE_HIGH_MULTIPLIER`` (defaults: 0.8 and 1.5).
        """
        now = _utcnow()

        task = await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,),
        )
        if not task:
            return {"error": "Task not found"}

        task_type = task.get("task_type") or "general"

        # Query historical completed tasks of the same type with both timestamps
        completed = await self._db.execute_fetchall(
            "SELECT started_at, completed_at FROM tasks "
            "WHERE task_type = ? AND status = 'completed' "
            "AND started_at IS NOT NULL AND completed_at IS NOT NULL",
            (task_type,),
        )

        if completed:
            durations_hours: list[float] = []
            for row in completed:
                try:
                    start = datetime.fromisoformat(row["started_at"])
                    end = datetime.fromisoformat(row["completed_at"])
                    hours = (end - start).total_seconds() / 3600.0
                    if hours >= 0:
                        durations_hours.append(hours)
                except (ValueError, TypeError) as exc:
                    logger.warning("Invalid timestamp in deadline estimation for task: %s", exc)
                    continue

            if durations_hours:
                avg_hours = sum(durations_hours) / len(durations_hours)
                samples = len(durations_hours)
            else:
                avg_hours = 1.0
                samples = 0
        else:
            avg_hours = 1.0
            samples = 0

        confidence_low = round(avg_hours * self.CONFIDENCE_LOW_MULTIPLIER, 4)
        confidence_high = round(avg_hours * self.CONFIDENCE_HIGH_MULTIPLIER, 4)
        estimated_hours = round(avg_hours, 4)

        eid = _new_id()
        await self._db.execute(
            "INSERT INTO deadline_estimates "
            "(id, task_id, estimated_hours, confidence_low, confidence_high, based_on_samples, estimated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, task_id, estimated_hours, confidence_low, confidence_high, samples, now),
        )
        return {
            "id": eid,
            "task_id": task_id,
            "estimated_hours": estimated_hours,
            "confidence_low": confidence_low,
            "confidence_high": confidence_high,
            "based_on_samples": samples,
            "estimated_at": now,
        }

    # ------------------------------------------------------------------
    # Feature 48: Scope Creep Detection
    # ------------------------------------------------------------------

    async def check_scope_creep(self, task_id: str, current_description: str) -> dict | None:
        """Compare current vs original description; flag if growth exceeds ``SCOPE_CREEP_GROWTH_THRESHOLD_PCT``."""
        now = _utcnow()

        task = await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,),
        )
        if not task:
            return None

        original = task.get("description") or ""
        if not original:
            return None

        original_length = len(original)
        current_length = len(current_description)

        if original_length == 0:
            return None

        growth_pct = ((current_length - original_length) / original_length) * 100.0

        # Also check keyword count growth (filter out pure numbers)
        orig_keywords = set(w for w in re.findall(r'\b\w+\b', original.lower()) if not w.isdigit())
        curr_keywords = set(w for w in re.findall(r'\b\w+\b', current_description.lower()) if not w.isdigit())
        keyword_growth = len(curr_keywords) - len(orig_keywords)
        keyword_growth_pct = (keyword_growth / max(len(orig_keywords), 1)) * 100.0

        # Flag if either length or keyword count grew by more than the threshold
        if growth_pct <= self.SCOPE_CREEP_GROWTH_THRESHOLD_PCT and keyword_growth_pct <= self.SCOPE_CREEP_GROWTH_THRESHOLD_PCT:
            return None

        effective_growth = max(growth_pct, keyword_growth_pct)

        fid = _new_id()
        await self._db.execute(
            "INSERT INTO scope_creep_flags "
            "(id, task_id, original_length, current_length, growth_pct, flagged_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fid, task_id, original_length, current_length, round(effective_growth, 2), now),
        )
        return {
            "id": fid,
            "task_id": task_id,
            "original_length": original_length,
            "current_length": current_length,
            "growth_pct": round(effective_growth, 2),
            "flagged_at": now,
        }

    async def get_scope_flags(self, task_id: str | None = None) -> list[dict]:
        """Query scope creep flags, optionally filtered by task_id."""
        if task_id:
            return await self._db.execute_fetchall(
                "SELECT * FROM scope_creep_flags WHERE task_id = ? ORDER BY flagged_at DESC",
                (task_id,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM scope_creep_flags ORDER BY flagged_at DESC",
        )

    # ------------------------------------------------------------------
    # Feature 49: Incremental Delivery Planning
    # ------------------------------------------------------------------

    async def plan_increments(
        self,
        feature_id: str,
        title: str,
        description: str,
    ) -> list[dict]:
        """Split description on 'and'/'then' to create delivery increments."""
        now = _utcnow()

        # Split on " and " or " then "
        parts = re.split(r'\s+and\s+|\s+then\s+', description, flags=re.IGNORECASE)
        parts = [p.strip() for p in parts if p.strip()]

        if not parts:
            parts = [description]

        increments: list[dict] = []
        for idx, part in enumerate(parts):
            iid = _new_id()
            inc_title = f"{title} - Part {idx + 1}" if len(parts) > 1 else title
            await self._db.execute(
                "INSERT INTO delivery_increments "
                "(id, feature_id, title, description, increment_order, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, 'planned', ?)",
                (iid, feature_id, inc_title, part, idx, now),
            )
            increments.append({
                "id": iid,
                "feature_id": feature_id,
                "title": inc_title,
                "description": part,
                "increment_order": idx,
                "status": "planned",
                "created_at": now,
            })

        return increments

    async def get_increments(self, feature_id: str) -> list[dict]:
        """Query increments ordered by increment_order."""
        return await self._db.execute_fetchall(
            "SELECT * FROM delivery_increments WHERE feature_id = ? ORDER BY increment_order",
            (feature_id,),
        )

    # ------------------------------------------------------------------
    # Feature 50: Automated Post-Mortems
    # ------------------------------------------------------------------

    async def generate_post_mortem(
        self,
        task_id: str | None = None,
        group_id: str | None = None,
    ) -> dict:
        """Analyze completed/failed tasks and compute stats.

        Computes: total tasks, completed count, failed count, success_rate,
        avg_duration_hours, common_failures (word frequency on rejection_reason),
        and lessons learned.
        """
        now = _utcnow()

        if task_id:
            tasks = await self._db.execute_fetchall(
                "SELECT * FROM tasks WHERE id = ?", (task_id,),
            )
        elif group_id:
            tasks = await self._db.execute_fetchall(
                "SELECT * FROM tasks WHERE group_id = ?", (group_id,),
            )
        else:
            return {"error": "Must provide task_id or group_id"}

        total = len(tasks)
        if total == 0:
            pid = _new_id()
            await self._db.execute(
                "INSERT INTO post_mortems "
                "(id, task_id, group_id, total_tasks, completed, failed, success_rate, "
                "avg_duration_hours, common_failures, lessons, created_at) "
                "VALUES (?, ?, ?, 0, 0, 0, 0, NULL, NULL, NULL, ?)",
                (pid, task_id, group_id, now),
            )
            return {
                "id": pid,
                "task_id": task_id,
                "group_id": group_id,
                "total_tasks": 0,
                "completed": 0,
                "failed": 0,
                "success_rate": 0.0,
                "avg_duration_hours": None,
                "common_failures": None,
                "lessons": None,
                "created_at": now,
            }

        completed_count = sum(1 for t in tasks if t.get("status") == "completed")
        failed_count = sum(1 for t in tasks if t.get("status") in ("failed", "rejected"))
        success_rate = round(completed_count / total, 4) if total > 0 else 0.0

        # Average duration for completed tasks
        durations: list[float] = []
        for t in tasks:
            if t.get("started_at") and t.get("completed_at"):
                try:
                    start = datetime.fromisoformat(t["started_at"])
                    end = datetime.fromisoformat(t["completed_at"])
                    hours = (end - start).total_seconds() / 3600.0
                    if hours >= 0:
                        durations.append(hours)
                except (ValueError, TypeError) as exc:
                    logger.warning("Invalid timestamp in post-mortem duration calc: %s", exc)
                    continue
        avg_duration = round(sum(durations) / len(durations), 4) if durations else None

        # Common failures: word frequency on rejection_reason
        word_counter: Counter = Counter()
        for t in tasks:
            reason = t.get("rejection_reason") or ""
            if reason:
                words = re.findall(r'\b\w+\b', reason.lower())
                word_counter.update(words)
        common = json.dumps(word_counter.most_common(10)) if word_counter else None

        # Lessons
        lessons_parts: list[str] = []
        if failed_count > 0:
            lessons_parts.append(f"{failed_count} task(s) failed or were rejected.")
        if success_rate < 0.5:
            lessons_parts.append("Low success rate suggests process improvements needed.")
        if success_rate >= 0.8:
            lessons_parts.append("High success rate indicates a healthy workflow.")
        lessons = " ".join(lessons_parts) if lessons_parts else None

        pid = _new_id()
        await self._db.execute(
            "INSERT INTO post_mortems "
            "(id, task_id, group_id, total_tasks, completed, failed, success_rate, "
            "avg_duration_hours, common_failures, lessons, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, task_id, group_id, total, completed_count, failed_count,
             success_rate, avg_duration, common, lessons, now),
        )
        return {
            "id": pid,
            "task_id": task_id,
            "group_id": group_id,
            "total_tasks": total,
            "completed": completed_count,
            "failed": failed_count,
            "success_rate": success_rate,
            "avg_duration_hours": avg_duration,
            "common_failures": common,
            "lessons": lessons,
            "created_at": now,
        }

    async def get_post_mortems(self, limit: int = 10) -> list[dict]:
        """Query post-mortems, most recent first."""
        return await self._db.execute_fetchall(
            "SELECT * FROM post_mortems ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
