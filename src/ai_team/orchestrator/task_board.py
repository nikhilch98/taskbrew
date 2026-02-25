"""Task board: group and task CRUD with dependency management."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

from ai_team.orchestrator.database import Database


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

        # Create dependency rows.
        if blocked_by:
            for dep_id in blocked_by:
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
    # Claim / Complete / Reject / Fail
    # ------------------------------------------------------------------

    async def claim_task(
        self, role: str, instance_id: str
    ) -> dict | None:
        """Atomically claim the highest-priority pending task for *role*.

        Uses a sub-select with ``RETURNING`` to guarantee atomicity even
        under concurrent access (SQLite serialises writes).

        Returns the claimed task dict, or ``None`` when the queue is empty.
        """
        # Build the priority CASE expression.
        priority_case = (
            "CASE priority "
            + " ".join(f"WHEN '{p}' THEN {v}" for p, v in _PRIORITY_ORDER.items())
            + " ELSE 99 END"
        )
        now = _utcnow()
        row = await self._db.execute_fetchone(
            f"UPDATE tasks "
            f"SET claimed_by = ?, status = 'in_progress', started_at = ? "
            f"WHERE id = ("
            f"  SELECT id FROM tasks "
            f"  WHERE assigned_to = ? AND status = 'pending' AND claimed_by IS NULL "
            f"  ORDER BY {priority_case}, created_at "
            f"  LIMIT 1"
            f") RETURNING *",
            (instance_id, now, role),
        )
        if row is not None:
            await self._db._conn.commit()  # type: ignore[union-attr]
        return row

    async def complete_task(self, task_id: str) -> dict:
        """Mark a task as completed and resolve downstream dependencies."""
        now = _utcnow()
        row = await self._db.execute_fetchone(
            "UPDATE tasks SET status = 'completed', completed_at = ? "
            "WHERE id = ? RETURNING *",
            (now, task_id),
        )
        assert row is not None, f"Task {task_id!r} not found"
        await self._db._conn.commit()  # type: ignore[union-attr]
        await self._resolve_dependencies(task_id)
        return row

    async def reject_task(self, task_id: str, reason: str) -> dict:
        """Mark a task as rejected with a reason."""
        row = await self._db.execute_fetchone(
            "UPDATE tasks SET status = 'rejected', rejection_reason = ? "
            "WHERE id = ? RETURNING *",
            (reason, task_id),
        )
        assert row is not None, f"Task {task_id!r} not found"
        await self._db._conn.commit()  # type: ignore[union-attr]
        return row

    async def fail_task(self, task_id: str) -> dict:
        """Mark a task as failed and cascade failure to blocked dependents."""
        row = await self._db.execute_fetchone(
            "UPDATE tasks SET status = 'failed' "
            "WHERE id = ? RETURNING *",
            (task_id,),
        )
        assert row is not None, f"Task {task_id!r} not found"
        await self._db._conn.commit()  # type: ignore[union-attr]
        await self._cascade_failure(task_id)
        return row

    async def _cascade_failure(self, failed_task_id: str) -> None:
        """When a task fails, fail all blocked tasks that depend on it.

        This prevents downstream tasks from being stuck in 'blocked' forever.
        Cascades recursively so the entire dependency chain is failed.
        """
        dependents = await self._db.execute_fetchall(
            "SELECT task_id FROM task_dependencies "
            "WHERE blocked_by = ? AND resolved = 0",
            (failed_task_id,),
        )
        for dep in dependents:
            tid = dep["task_id"]
            task = await self._db.execute_fetchone(
                "SELECT status FROM tasks WHERE id = ?", (tid,)
            )
            if task and task["status"] == "blocked":
                await self._db.execute(
                    "UPDATE tasks SET status = 'failed' WHERE id = ?",
                    (tid,),
                )
                # Mark this dependency as resolved so it doesn't block cleanup
                await self._db.execute(
                    "UPDATE task_dependencies SET resolved = 1 "
                    "WHERE task_id = ? AND blocked_by = ?",
                    (tid, failed_task_id),
                )
                # Cascade further down the chain
                await self._cascade_failure(tid)
        await self._db._conn.commit()  # type: ignore[union-attr]

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
        rows = await self._db.execute_fetchall(
            "UPDATE tasks SET status = 'pending', claimed_by = NULL, started_at = NULL "
            "WHERE status = 'in_progress' RETURNING *"
        )
        if rows:
            await self._db._conn.commit()
        return rows

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

        if repaired:
            await self._db._conn.commit()  # type: ignore[union-attr]

        return repaired
