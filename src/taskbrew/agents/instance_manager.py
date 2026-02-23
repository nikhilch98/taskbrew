"""Agent instance manager for tracking running agent instances."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from taskbrew.config_loader import RoleConfig
from taskbrew.orchestrator.database import Database


def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class InstanceManager:
    """Manages agent instance registration, status updates, and heartbeats.

    Each agent instance is tracked in the ``agent_instances`` database table
    with its role, current status, and heartbeat timestamp.

    Parameters
    ----------
    db:
        An initialised :class:`Database` instance.
    """

    VALID_STATUSES = {"idle", "working", "paused", "stopped"}

    def __init__(self, db: Database) -> None:
        self._db = db
        self._paused_roles: set[str] = set()

    def pause_role(self, role: str) -> None:
        self._paused_roles.add(role)

    def resume_role(self, role: str) -> None:
        self._paused_roles.discard(role)

    def is_role_paused(self, role: str) -> bool:
        return role in self._paused_roles

    def get_paused_roles(self) -> list[str]:
        return sorted(self._paused_roles)

    def pause_all(self, roles: list[str]) -> None:
        self._paused_roles.update(roles)

    def resume_all(self) -> None:
        self._paused_roles.clear()

    async def register_instance(
        self, instance_id: str, role_config: RoleConfig
    ) -> dict:
        """Register (or re-register) an agent instance.

        Uses INSERT OR REPLACE so that calling this for an already-registered
        instance effectively resets its state.

        Returns the instance dict.
        """
        now = _utcnow()
        await self._db.execute(
            "INSERT OR REPLACE INTO agent_instances "
            "(instance_id, role, status, current_task, started_at, last_heartbeat) "
            "VALUES (?, ?, 'idle', NULL, ?, NULL)",
            (instance_id, role_config.role, now),
        )
        return {
            "instance_id": instance_id,
            "role": role_config.role,
            "status": "idle",
            "current_task": None,
            "started_at": now,
            "last_heartbeat": None,
        }

    async def update_status(
        self,
        instance_id: str,
        status: str,
        current_task: str | None = None,
    ) -> dict:
        """Update the status and optional current_task for an instance.

        Returns the updated instance dict.
        """
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status!r}. Must be one of {self.VALID_STATUSES}")
        await self._db.execute(
            "UPDATE agent_instances SET status = ?, current_task = ? "
            "WHERE instance_id = ?",
            (status, current_task, instance_id),
        )
        row = await self._db.execute_fetchone(
            "SELECT * FROM agent_instances WHERE instance_id = ?",
            (instance_id,),
        )
        if row is None:
            raise ValueError(f"Instance {instance_id!r} not found")
        return row

    async def heartbeat(self, instance_id: str) -> None:
        """Update the last_heartbeat timestamp to the current time."""
        now = _utcnow()
        await self._db.execute(
            "UPDATE agent_instances SET last_heartbeat = ? WHERE instance_id = ?",
            (now, instance_id),
        )

    async def get_instance(self, instance_id: str) -> dict | None:
        """Return a single instance by ID, or None if not found."""
        return await self._db.execute_fetchone(
            "SELECT * FROM agent_instances WHERE instance_id = ?",
            (instance_id,),
        )

    async def get_all_instances(self) -> list[dict]:
        """Return all registered instances ordered by instance_id."""
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_instances ORDER BY instance_id"
        )

    async def get_instances_by_role(self, role: str) -> list[dict]:
        """Return all instances with a given role."""
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_instances WHERE role = ? ORDER BY instance_id",
            (role,),
        )

    async def get_stale_instances(self, timeout_seconds: float = 60.0) -> list[dict]:
        """Return working instances whose heartbeat is older than *timeout_seconds*.

        Only instances with status ``'working'`` are returned — idle/paused
        agents are not holding tasks.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        ).isoformat()
        return await self._db.execute_fetchall(
            "SELECT * FROM agent_instances "
            "WHERE status = 'working' "
            "  AND ("
            "    (last_heartbeat IS NOT NULL AND last_heartbeat < ?) "
            "    OR (last_heartbeat IS NULL AND started_at < ?)"
            "  ) "
            "ORDER BY instance_id",
            (cutoff, cutoff),
        )

    async def remove_instance(self, instance_id: str) -> None:
        """Remove an agent instance from the registry."""
        await self._db.execute(
            "DELETE FROM agent_instances WHERE instance_id = ?",
            (instance_id,),
        )

    # ------------------------------------------------------------------
    # Performance tracking
    # ------------------------------------------------------------------

    async def get_performance_stats(self, instance_id: str | None = None) -> list[dict]:
        """Get performance statistics for agents.

        Parameters
        ----------
        instance_id:
            If provided, return stats only for this instance.  Otherwise
            return stats for all agents ordered by task count descending.
        """
        if instance_id:
            return await self._db.execute_fetchall(
                "SELECT agent_id, COUNT(*) as tasks, "
                "AVG(duration_api_ms) as avg_duration, "
                "SUM(cost_usd) as total_cost, "
                "AVG(num_turns) as avg_turns "
                "FROM task_usage WHERE agent_id = ? GROUP BY agent_id",
                (instance_id,),
            )
        return await self._db.execute_fetchall(
            "SELECT agent_id, COUNT(*) as tasks, "
            "AVG(duration_api_ms) as avg_duration, "
            "SUM(cost_usd) as total_cost, "
            "AVG(num_turns) as avg_turns "
            "FROM task_usage GROUP BY agent_id ORDER BY tasks DESC"
        )

    async def get_role_performance(self) -> list[dict]:
        """Get aggregated performance by role.

        Derives the role name from the agent_id prefix (everything before
        the first ``'-'``).
        """
        return await self._db.execute_fetchall(
            "SELECT SUBSTR(agent_id, 1, INSTR(agent_id, '-') - 1) as role, "
            "COUNT(*) as tasks, "
            "AVG(duration_api_ms) as avg_duration, "
            "SUM(cost_usd) as total_cost "
            "FROM task_usage GROUP BY role ORDER BY tasks DESC"
        )
