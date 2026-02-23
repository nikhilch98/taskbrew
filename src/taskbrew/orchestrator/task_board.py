"""Task board: group and task CRUD with dependency management."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import datetime, timezone

from taskbrew.orchestrator.database import Database

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# Priority ordering used by the claim query (lower number = higher priority).
_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


class TaskBoard:
    """High-level CRUD interface for groups, tasks, and dependencies.

    Parameters
    ----------
    db:
        An initialised :class:`Database` instance.
    group_prefixes:
        Optional mapping of ``role -> prefix`` used when creating groups
        (e.g. ``{"pm": "FEAT", "architect": "DEBT"}``).
    """

    def __init__(
        self,
        db: Database,
        group_prefixes: dict[str, str] | None = None,
    ) -> None:
        self._db = db
        self._group_prefixes: dict[str, str] = dict(group_prefixes or {})
        # Mapping from role name to task-ID prefix (e.g. "coder" -> "CD").
        self._role_to_prefix: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Prefix helpers
    # ------------------------------------------------------------------

    def set_group_prefixes(self, prefixes: dict[str, str]) -> None:
        """Replace the group prefix mapping."""
        self._group_prefixes = dict(prefixes)

    async def register_prefixes(self, role_prefixes: dict[str, str]) -> None:
        """Register all role prefixes in the database and cache the mapping.

        Parameters
        ----------
        role_prefixes:
            Mapping of ``role_name -> prefix`` (e.g. ``{"coder": "CD"}``).
        """
        self._role_to_prefix = dict(role_prefixes)
        for prefix in role_prefixes.values():
            await self._db.register_prefix(prefix)
        # Also register group prefixes.
        for prefix in self._group_prefixes.values():
            await self._db.register_prefix(prefix)

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    async def create_group(
        self,
        title: str,
        origin: str | None = None,
        created_by: str | None = None,
    ) -> dict:
        """Create a new group with an auto-generated ID.

        The prefix is determined by *created_by* via ``_group_prefixes``.
        Falls back to ``"GRP"`` if the role has no group prefix configured.
        """
        prefix = self._group_prefixes.get(created_by or "", "GRP")
        # Make sure the prefix is registered (idempotent).
        await self._db.register_prefix(prefix)
        group_id = await self._db.generate_task_id(prefix)
        now = _utcnow()
        await self._db.execute(
            "INSERT INTO groups (id, title, origin, status, created_by, created_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (group_id, title, origin, created_by, now),
        )
        return {
            "id": group_id,
            "title": title,
            "origin": origin,
            "status": "active",
            "created_by": created_by,
            "created_at": now,
            "completed_at": None,
        }

    async def get_groups(self, status: str | None = None) -> list[dict]:
        """Return all groups, optionally filtered by status."""
        if status is not None:
            return await self._db.execute_fetchall(
                "SELECT * FROM groups WHERE status = ? ORDER BY created_at",
                (status,),
            )
        return await self._db.execute_fetchall(
            "SELECT * FROM groups ORDER BY created_at"
        )

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    async def create_task(
        self,
        group_id: str,
        title: str,
        task_type: str,
        assigned_to: str,
        created_by: str | None = None,
        description: str | None = None,
        priority: str = "medium",
        parent_id: str | None = None,
        revision_of: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> dict:
        """Create a new task with an auto-generated ID.

        The ID prefix is derived from the *assigned_to* role using the
        ``_role_to_prefix`` mapping populated by :meth:`register_prefixes`.
        """
        prefix = self._role_to_prefix.get(assigned_to, assigned_to.upper()[:2])
        # Ensure the prefix is registered.
        await self._db.register_prefix(prefix)

        task_id = await self._db.generate_task_id(prefix)
        now = _utcnow()
        status = "blocked" if blocked_by else "pending"

        await self._db.execute(
            "INSERT INTO tasks "
            "(id, group_id, parent_id, title, description, task_type, "
            " priority, assigned_to, status, created_by, created_at, revision_of) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                group_id,
                parent_id,
                title,
                description,
                task_type,
                priority,
                assigned_to,
                status,
                created_by,
                now,
                revision_of,
            ),
        )

        # Create dependency rows (with cycle detection).
        if blocked_by:
            for dep_id in blocked_by:
                if await self.has_cycle(task_id, dep_id):
                    raise ValueError(
                        f"Dependency {task_id} -> {dep_id} would create a cycle"
                    )
                await self._db.execute(
                    "INSERT INTO task_dependencies (task_id, blocked_by) VALUES (?, ?)",
                    (task_id, dep_id),
                )

        return {
            "id": task_id,
            "group_id": group_id,
            "parent_id": parent_id,
            "title": title,
            "description": description,
            "task_type": task_type,
            "priority": priority,
            "assigned_to": assigned_to,
            "claimed_by": None,
            "status": status,
            "created_by": created_by,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "rejection_reason": None,
            "revision_of": revision_of,
        }

    async def get_task(self, task_id: str) -> dict | None:
        """Return a single task by ID, or None."""
        return await self._db.execute_fetchone(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        )

    async def get_group_tasks(self, group_id: str) -> list[dict]:
        """Return all tasks belonging to a group."""
        return await self._db.execute_fetchall(
            "SELECT * FROM tasks WHERE group_id = ? ORDER BY created_at",
            (group_id,),
        )

    # ------------------------------------------------------------------
    # Usage tracking (public delegation)
    # ------------------------------------------------------------------

    async def record_task_usage(self, task_id: str, agent_id: str, **kwargs) -> None:
        """Record usage metrics for a task execution."""
        await self._db.record_task_usage(task_id=task_id, agent_id=agent_id, **kwargs)

    # ------------------------------------------------------------------
    # Claim / Complete / Reject / Fail
    # ------------------------------------------------------------------

    async def claim_task(
        self, role: str, instance_id: str
    ) -> dict | None:
        """Atomically claim the highest-priority pending task for *role*.

        Uses an explicit transaction with SELECT-then-UPDATE to prevent
        race conditions where two agents could claim the same task.

        Returns the claimed task dict, or ``None`` when the queue is empty.
        """
        # Build the priority CASE expression.
        priority_case = (
            "CASE priority "
            + " ".join(f"WHEN '{p}' THEN {v}" for p, v in _PRIORITY_ORDER.items())
            + " ELSE 99 END"
        )
        now = _utcnow()

        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                f"SELECT * FROM tasks "
                f"WHERE assigned_to = ? AND status = 'pending' AND claimed_by IS NULL "
                f"ORDER BY {priority_case}, created_at "
                f"LIMIT 1",
                (role,),
            )
            row = await cursor.fetchone()
            if not row:
                return None

            # Convert aiosqlite.Row to dict
            keys = [desc[0] for desc in cursor.description]
            task = dict(zip(keys, row))

            cursor = await conn.execute(
                "UPDATE tasks SET claimed_by = ?, status = 'in_progress', started_at = ? "
                "WHERE id = ? RETURNING *",
                (instance_id, now, task["id"]),
            )
            updated_row = await cursor.fetchone()
            if not updated_row:
                return None
            updated_keys = [desc[0] for desc in cursor.description]
            result = dict(zip(updated_keys, updated_row))

        logger.info("Task %s claimed by %s", result["id"], instance_id)
        return result

    async def complete_task(self, task_id: str) -> dict:
        """Mark a task as completed and resolve downstream dependencies."""
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'completed', completed_at = ? "
            "WHERE id = ? AND status = 'in_progress' RETURNING *",
            (now, task_id),
        )
        if not rows:
            # Check whether the task exists at all vs. is in a terminal state.
            existing = await self._db.execute_fetchone(
                "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
            )
            if existing is None:
                raise ValueError(f"Task not found: {task_id}")
            logger.warning(
                "complete_task(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                existing["status"],
            )
            return existing
        await self._resolve_dependencies(task_id)
        await self._check_group_completion(task_id)
        logger.info("Task %s completed", task_id)
        return rows[0]

    async def complete_task_with_output(self, task_id: str, output: str) -> dict:
        """Mark task as completed and store the agent output."""
        now = _utcnow()
        # Truncate output for storage (keep first 2000 chars)
        truncated = output[:2000] if output else ""
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'completed', completed_at = ?, "
            "output_text = ? "
            "WHERE id = ? AND status = 'in_progress' RETURNING *",
            (now, truncated, task_id),
        )
        if not rows:
            existing = await self._db.execute_fetchone(
                "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
            )
            if existing is None:
                raise ValueError(f"Task not found: {task_id}")
            logger.warning(
                "complete_task_with_output(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                existing["status"],
            )
            return existing
        await self._resolve_dependencies(task_id)
        await self._check_group_completion(task_id)
        return rows[0]

    async def reject_task(self, task_id: str, reason: str) -> dict:
        """Mark a task as rejected with a reason."""
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'rejected', rejection_reason = ? "
            "WHERE id = ? RETURNING *",
            (reason, task_id),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")
        return rows[0]

    async def fail_task(self, task_id: str) -> dict:
        """Mark a task as failed and cascade failure to blocked dependents."""
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'failed' "
            "WHERE id = ? AND status = 'in_progress' RETURNING *",
            (task_id,),
        )
        if not rows:
            existing = await self._db.execute_fetchone(
                "SELECT id, status FROM tasks WHERE id = ?", (task_id,)
            )
            if existing is None:
                raise ValueError(f"Task not found: {task_id}")
            logger.warning(
                "fail_task(%s) skipped: task is in status '%s', "
                "expected 'in_progress'",
                task_id,
                existing["status"],
            )
            return existing
        await self._cascade_failure(task_id)
        # Also cancel child tasks linked via parent_id that are still pending.
        await self._db.execute(
            "UPDATE tasks SET status = 'cancelled' "
            "WHERE parent_id = ? AND status = 'pending'",
            (task_id,),
        )
        await self._check_group_completion(task_id)
        logger.info("Task %s failed", task_id)
        return rows[0]

    async def _cascade_failure(self, task_id: str) -> None:
        """When a task fails, fail all blocked tasks that depend on it.

        This prevents downstream tasks from being stuck in 'blocked' forever.
        Uses iterative BFS to cascade through the entire dependency chain.
        """
        queue: deque[str] = deque([task_id])
        while queue:
            current = queue.popleft()
            dependents = await self._db.execute_fetchall(
                "SELECT task_id FROM task_dependencies WHERE blocked_by = ? AND resolved = 0",
                (current,),
            )
            for dep in dependents:
                dep_task = await self._db.execute_fetchone(
                    "SELECT * FROM tasks WHERE id = ? AND status IN ('pending', 'blocked')",
                    (dep["task_id"],),
                )
                if dep_task:
                    await self._db.execute(
                        "UPDATE tasks SET status = 'failed' WHERE id = ?",
                        (dep_task["id"],),
                    )
                    queue.append(dep_task["id"])

    # ------------------------------------------------------------------
    # Group completion check
    # ------------------------------------------------------------------

    async def _check_group_completion(self, task_id: str) -> None:
        """Check if all tasks in the group are in terminal states.

        If every task in the group has status ``completed``, ``failed``, or
        ``cancelled``, the group is marked as ``completed`` with the current
        timestamp.
        """
        # Look up the group_id for this task.
        task = await self._db.execute_fetchone(
            "SELECT group_id FROM tasks WHERE id = ?", (task_id,)
        )
        if not task or not task["group_id"]:
            return

        group_id = task["group_id"]

        # Check whether any task in this group is NOT in a terminal state.
        non_terminal = await self._db.execute_fetchone(
            "SELECT 1 FROM tasks WHERE group_id = ? "
            "AND status NOT IN ('completed', 'failed', 'cancelled') LIMIT 1",
            (group_id,),
        )
        if non_terminal:
            return

        # All tasks are terminal -- mark the group as completed.
        now = _utcnow()
        await self._db.execute(
            "UPDATE groups SET status = 'completed', completed_at = ? "
            "WHERE id = ? AND status = 'active'",
            (now, group_id),
        )

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    async def _resolve_dependencies(self, completed_task_id: str) -> None:
        """Resolve dependencies after a task completes.

        1. Mark all ``task_dependencies`` rows where ``blocked_by`` equals the
           completed task as ``resolved = 1``.
        2. Find any blocked tasks that now have *zero* unresolved dependencies
           and transition them to ``'pending'``.
        """
        now = _utcnow()
        await self._db.execute(
            "UPDATE task_dependencies SET resolved = 1, resolved_at = ? "
            "WHERE blocked_by = ? AND resolved = 0",
            (now, completed_task_id),
        )

        # Find tasks that were blocked and now have no remaining unresolved deps.
        newly_free = await self._db.execute_fetchall(
            "SELECT t.id FROM tasks t "
            "WHERE t.status = 'blocked' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM task_dependencies d "
            "    WHERE d.task_id = t.id AND d.resolved = 0"
            "  )",
        )
        for row in newly_free:
            await self._db.execute(
                "UPDATE tasks SET status = 'pending' WHERE id = ?",
                (row["id"],),
            )

    # ------------------------------------------------------------------
    # Board view
    # ------------------------------------------------------------------

    async def get_board(
        self,
        group_id: str | None = None,
        assigned_to: str | None = None,
        claimed_by: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
    ) -> dict[str, list[dict]]:
        """Return tasks grouped by status, with optional filters.

        Returns a dict like ``{"pending": [...], "in_progress": [...], ...}``.
        """
        clauses: list[str] = []
        params: list[str] = []

        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if claimed_by is not None:
            clauses.append("claimed_by = ?")
            params.append(claimed_by)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks{where} ORDER BY created_at"
        rows = await self._db.execute_fetchall(sql, tuple(params))

        board: dict[str, list[dict]] = {}
        for row in rows:
            status = row["status"]
            board.setdefault(status, []).append(row)
        return board

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    async def has_cycle(self, task_id: str, blocked_by_id: str) -> bool:
        """Return True if adding ``task_id`` blocked-by ``blocked_by_id``
        would create a cycle in the dependency graph.

        Uses BFS starting from *blocked_by_id*, following the
        ``task_dependencies`` edges (unresolved) in reverse (i.e. "who is
        *blocked_by_id* blocked by?").  If we reach *task_id* there is a
        cycle.

        Additionally, a direct identity check is performed: if *task_id*
        equals *blocked_by_id*, that is a trivial cycle.
        """
        if task_id == blocked_by_id:
            return True

        # BFS: starting from blocked_by_id, walk "upstream" through
        # unresolved dependencies.  If we ever reach task_id there would be
        # a cycle.
        visited: set[str] = set()
        queue: deque[str] = deque([blocked_by_id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            # Find what *current* is blocked by.
            rows = await self._db.execute_fetchall(
                "SELECT blocked_by FROM task_dependencies "
                "WHERE task_id = ? AND resolved = 0",
                (current,),
            )
            for row in rows:
                upstream = row["blocked_by"]
                if upstream == task_id:
                    return True
                if upstream not in visited:
                    queue.append(upstream)

        return False

    # ------------------------------------------------------------------
    # Resilience / Recovery
    # ------------------------------------------------------------------

    async def recover_orphaned_tasks(self) -> list[dict]:
        """Reset in_progress tasks to pending on server restart.

        When the server crashes, any tasks that were in_progress become
        orphaned since all agents are new on restart.
        """
        return await self._db.execute_returning(
            "UPDATE tasks SET status = 'pending', claimed_by = NULL, started_at = NULL "
            "WHERE status = 'in_progress' RETURNING *"
        )

    async def recover_stale_in_progress_tasks(
        self, stale_instance_ids: list[str]
    ) -> list[dict]:
        """Reset in_progress tasks claimed by stale (dead) instances.

        Unlike :meth:`recover_orphaned_tasks` which resets *all* in_progress
        tasks (suitable only for server restart), this method targets tasks
        held by specific instances whose heartbeats have gone stale -- safe to
        call during normal operation.

        After resetting, resolves dependencies for any tasks that were blocked
        by the recovered tasks so they can transition to ``'pending'``.
        """
        if not stale_instance_ids:
            return []
        placeholders = ", ".join("?" for _ in stale_instance_ids)
        recovered = await self._db.execute_returning(
            f"UPDATE tasks SET status = 'pending', claimed_by = NULL, started_at = NULL "
            f"WHERE status = 'in_progress' AND claimed_by IN ({placeholders}) "
            f"RETURNING *",
            tuple(stale_instance_ids),
        )

        # Resolve dependencies for tasks that were blocked by the recovered
        # tasks.  The recovered tasks are back to pending (not completed), but
        # other tasks may have been waiting on them in a blocked state that
        # should be re-evaluated now that the stale claim is cleared.
        for task in recovered:
            await self._resolve_dependencies(task["id"])

        return recovered

    async def recover_stuck_blocked_tasks(self) -> list[dict]:
        """Recover blocked tasks whose dependencies are all in terminal states.

        A blocked task should be failed if any of its unresolved dependencies
        failed, or moved to pending if all dependencies completed but the
        resolution was missed (e.g. crash).
        """
        # Find blocked tasks with unresolved deps pointing to terminal tasks
        stuck = await self._db.execute_fetchall(
            "SELECT DISTINCT d.task_id, d.blocked_by, t2.status AS blocker_status "
            "FROM task_dependencies d "
            "JOIN tasks t ON t.id = d.task_id AND t.status = 'blocked' "
            "JOIN tasks t2 ON t2.id = d.blocked_by "
            "WHERE d.resolved = 0 "
            "  AND t2.status IN ('completed', 'failed')"
        )
        if not stuck:
            return []

        repaired: list[dict] = []
        seen: set[str] = set()

        for row in stuck:
            tid = row["task_id"]
            blocker_status = row["blocker_status"]

            # Resolve this dependency
            await self._db.execute(
                "UPDATE task_dependencies SET resolved = 1 "
                "WHERE task_id = ? AND blocked_by = ?",
                (tid, row["blocked_by"]),
            )

            # If blocker failed, cascade failure to this task
            if blocker_status == "failed" and tid not in seen:
                await self._db.execute(
                    "UPDATE tasks SET status = 'failed' WHERE id = ? AND status = 'blocked'",
                    (tid,),
                )
                seen.add(tid)
                task = await self._db.execute_fetchone(
                    "SELECT * FROM tasks WHERE id = ?", (tid,)
                )
                if task:
                    repaired.append(task)
                    # Cascade further
                    await self._cascade_failure(tid)

        # Check for tasks now fully unblocked (all deps resolved successfully)
        newly_free = await self._db.execute_fetchall(
            "SELECT t.id FROM tasks t "
            "WHERE t.status = 'blocked' "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM task_dependencies d "
            "    WHERE d.task_id = t.id AND d.resolved = 0"
            "  )",
        )
        for row in newly_free:
            await self._db.execute(
                "UPDATE tasks SET status = 'pending' WHERE id = ?",
                (row["id"],),
            )
            task = await self._db.execute_fetchone(
                "SELECT * FROM tasks WHERE id = ?", (row["id"],)
            )
            if task:
                repaired.append(task)

        return repaired

    # ------------------------------------------------------------------
    # Task Cancellation
    # ------------------------------------------------------------------

    async def cancel_task(self, task_id: str, reason: str | None = None) -> dict:
        """Cancel a task and cascade failure to blocked dependents.

        Sets the task status to ``'cancelled'``, records the cancellation
        reason in ``rejection_reason``, and cascades failure to any tasks
        that are blocked by this one (reuses :meth:`_cascade_failure`).

        Returns the cancelled task dict.
        """
        now = _utcnow()
        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'cancelled', completed_at = ?, "
            "rejection_reason = ? WHERE id = ? RETURNING *",
            (now, reason, task_id),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")
        await self._cascade_failure(task_id)
        await self._check_group_completion(task_id)
        return rows[0]

    # ------------------------------------------------------------------
    # Task Search
    # ------------------------------------------------------------------

    async def search_tasks(
        self,
        query: str,
        group_id: str | None = None,
        status: str | None = None,
        assigned_to: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Full-text search on task title and description with optional filters.

        Returns a pagination-aware dict::

            {"tasks": [...], "total": int, "limit": int, "offset": int}
        """
        clauses: list[str] = []
        params: list = []

        # Full-text search on title and description.
        clauses.append("(title LIKE ? OR description LIKE ?)")
        like_pattern = f"%{query}%"
        params.extend([like_pattern, like_pattern])

        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if assigned_to is not None:
            clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)

        where = " WHERE " + " AND ".join(clauses)

        # Count total matching rows.
        count_row = await self._db.execute_fetchone(
            f"SELECT COUNT(*) AS total FROM tasks{where}",
            tuple(params),
        )
        total = count_row["total"] if count_row else 0

        # Fetch the page.
        tasks = await self._db.execute_fetchall(
            f"SELECT * FROM tasks{where} ORDER BY created_at LIMIT ? OFFSET ?",
            tuple(params) + (limit, offset),
        )

        return {"tasks": tasks, "total": total, "limit": limit, "offset": offset}

    # ------------------------------------------------------------------
    # Task Retry
    # ------------------------------------------------------------------

    async def retry_task(self, task_id: str) -> dict:
        """Reset a failed, rejected, or cancelled task back to pending.

        Clears ``claimed_by`` and ``completed_at``, sets status to
        ``'pending'``.

        Returns the reset task dict.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] not in ("failed", "rejected", "cancelled"):
            raise ValueError(
                f"Can only retry failed/rejected/cancelled tasks, "
                f"got status={task['status']!r}"
            )

        rows = await self._db.execute_returning(
            "UPDATE tasks SET status = 'pending', claimed_by = NULL, "
            "completed_at = NULL WHERE id = ? RETURNING *",
            (task_id,),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")

        return rows[0]

    # ------------------------------------------------------------------
    # Task Reassignment
    # ------------------------------------------------------------------

    async def reassign_task(self, task_id: str, new_assignee: str) -> dict:
        """Reassign a pending or blocked task to a new agent/role.

        Returns the updated task dict.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task["status"] not in ("pending", "blocked"):
            raise ValueError(
                f"Can only reassign pending/blocked tasks, "
                f"got status={task['status']!r}"
            )

        rows = await self._db.execute_returning(
            "UPDATE tasks SET assigned_to = ? WHERE id = ? RETURNING *",
            (new_assignee, task_id),
        )
        if not rows:
            raise ValueError(f"Task not found: {task_id}")
        return rows[0]

    # ------------------------------------------------------------------
    # Batch Operations
    # ------------------------------------------------------------------

    async def batch_update_tasks(
        self,
        task_ids: list[str],
        action: str,
        params: dict | None = None,
    ) -> dict:
        """Apply a batch action to multiple tasks.

        Supported actions:

        - ``"cancel"``: Cancel all specified tasks.
        - ``"reassign"``: Set ``assigned_to`` for pending/blocked tasks.
          Requires ``params["assigned_to"]``.
        - ``"change_priority"``: Update priority for specified tasks.
          Requires ``params["priority"]``.
        - ``"retry"``: Reset failed/rejected/cancelled tasks to pending.

        Returns ``{"updated": int, "task_ids": [str]}``.
        """
        params = params or {}
        updated_ids: list[str] = []

        if action == "cancel":
            for tid in task_ids:
                try:
                    await self.cancel_task(tid, reason=params.get("reason"))
                    updated_ids.append(tid)
                except ValueError:
                    continue

        elif action == "reassign":
            new_assignee = params.get("assigned_to", "")
            for tid in task_ids:
                try:
                    await self.reassign_task(tid, new_assignee)
                    updated_ids.append(tid)
                except ValueError:
                    continue

        elif action == "change_priority":
            new_priority = params.get("priority", "medium")
            for tid in task_ids:
                rows = await self._db.execute_returning(
                    "UPDATE tasks SET priority = ? WHERE id = ? RETURNING *",
                    (new_priority, tid),
                )
                if rows:
                    updated_ids.append(tid)

        elif action == "retry":
            for tid in task_ids:
                try:
                    await self.retry_task(tid)
                    updated_ids.append(tid)
                except ValueError:
                    continue

        else:
            raise ValueError(f"Unknown batch action: {action!r}")

        return {"updated": len(updated_ids), "task_ids": updated_ids}

    # ------------------------------------------------------------------
    # Task Templates
    # ------------------------------------------------------------------

    async def create_template(
        self,
        name: str,
        title_template: str,
        description_template: str,
        task_type: str,
        assigned_to: str,
        priority: str = "medium",
    ) -> dict:
        """Insert a new task template. Returns the template dict."""
        await self._db.register_prefix("TPL")
        template_id = await self._db.generate_task_id("TPL")
        now = _utcnow()

        await self._db.execute(
            "INSERT INTO task_templates "
            "(id, name, title_template, description_template, task_type, "
            "assigned_to, priority, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (template_id, name, title_template, description_template,
             task_type, assigned_to, priority, now),
        )

        return {
            "id": template_id,
            "name": name,
            "title_template": title_template,
            "description_template": description_template,
            "task_type": task_type,
            "assigned_to": assigned_to,
            "priority": priority,
            "created_at": now,
        }

    async def get_templates(self) -> list[dict]:
        """Return all task templates."""
        return await self._db.execute_fetchall(
            "SELECT * FROM task_templates ORDER BY created_at"
        )

    async def create_from_template(
        self,
        template_name: str,
        group_id: str,
        variables: dict[str, str] | None = None,
    ) -> dict:
        """Create a task from a named template with variable substitution.

        Placeholders like ``{variable}`` in the title and description
        templates are replaced with values from *variables*.

        Returns the created task dict.
        """
        template = await self._db.execute_fetchone(
            "SELECT * FROM task_templates WHERE name = ?",
            (template_name,),
        )
        if template is None:
            raise ValueError(f"Template not found: {template_name!r}")

        variables = variables or {}

        title = template["title_template"]
        description = template["description_template"] or ""
        for key, value in variables.items():
            title = title.replace(f"{{{key}}}", value)
            description = description.replace(f"{{{key}}}", value)

        return await self.create_task(
            group_id=group_id,
            title=title,
            description=description or None,
            task_type=template["task_type"],
            assigned_to=template["assigned_to"],
            priority=template["priority"],
        )

    # ------------------------------------------------------------------
    # Custom Workflow Execution
    # ------------------------------------------------------------------

    async def start_workflow(
        self, workflow_id: str, group_id: str
    ) -> list[dict]:
        """Load a workflow definition and create tasks with dependencies.

        The workflow ``steps`` field is a JSON array of objects, each with
        at least ``title``, ``task_type``, and ``assigned_to``.  Steps are
        chained sequentially: each step is blocked by the previous one.

        Returns the list of created tasks.
        """
        workflow = await self._db.execute_fetchone(
            "SELECT * FROM workflow_definitions WHERE id = ?",
            (workflow_id,),
        )
        if workflow is None:
            raise ValueError(f"Workflow not found: {workflow_id}")

        steps = json.loads(workflow["steps"])
        created_tasks: list[dict] = []
        prev_task_id: str | None = None

        for step in steps:
            blocked_by = [prev_task_id] if prev_task_id else None
            task = await self.create_task(
                group_id=group_id,
                title=step["title"],
                task_type=step.get("task_type", "workflow_step"),
                assigned_to=step.get("assigned_to", "coder"),
                description=step.get("description"),
                priority=step.get("priority", "medium"),
                blocked_by=blocked_by,
            )
            created_tasks.append(task)
            prev_task_id = task["id"]

        return created_tasks

    # ------------------------------------------------------------------
    # Retry Classification
    # ------------------------------------------------------------------

    async def classify_failure(self, task_id: str) -> str:
        """Classify a task failure as transient, logic, or permanent.

        Uses simple keyword matching on the ``rejection_reason`` field:

        - **transient**: network errors, timeouts, rate limits (auto-retry).
        - **logic**: code bugs, assertion errors (needs fix).
        - **permanent**: missing resources, not found (skip).

        Returns one of ``"transient"``, ``"logic"``, or ``"permanent"``.
        """
        task = await self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        reason = (task.get("rejection_reason") or "").lower()

        transient_keywords = [
            "timeout", "timed out", "network", "connection",
            "rate limit", "ratelimit", "retry", "503", "502",
            "504", "temporary", "unavailable",
        ]
        permanent_keywords = [
            "not found", "missing", "does not exist", "404",
            "forbidden", "403", "deleted", "gone", "no such",
            "permission denied",
        ]

        for keyword in transient_keywords:
            if keyword in reason:
                return "transient"

        for keyword in permanent_keywords:
            if keyword in reason:
                return "permanent"

        # Default: if there is a reason but no match, assume logic error.
        return "logic"
