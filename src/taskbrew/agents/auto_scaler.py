"""Dynamic agent instance scaling based on queue depth."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from taskbrew.agents.instance_manager import InstanceManager
from taskbrew.config_loader import RoleConfig
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard

logger = logging.getLogger(__name__)

# Minimum seconds between consecutive scale-up or scale-down actions per role.
DEFAULT_COOLDOWN_SECONDS = 60
# Minimum seconds an agent must be idle before it becomes eligible for
# scale-down (5 minutes).
DEFAULT_IDLE_THRESHOLD_SECONDS = 300


class AutoScaler:
    """Monitors task queue and scales agent instances up/down.

    Parameters
    ----------
    task_board:
        The :class:`TaskBoard` used to query pending task counts.
    instance_manager:
        The :class:`InstanceManager` used to inspect running instances.
    roles:
        Mapping of role name to :class:`RoleConfig`.  Only roles with
        ``auto_scale.enabled`` set to ``True`` will be considered.
    agent_factory:
        Optional async callback ``(instance_id, role_config) -> Task`` invoked
        to spawn a new agent when scaling up.
    agent_stopper:
        Optional async callback ``(instance_id) -> None`` invoked to stop an
        idle agent when scaling down.
    cooldown_seconds:
        Minimum seconds between consecutive scaling actions for a single role.
    idle_threshold_seconds:
        Minimum seconds an agent must be idle before it is eligible for
        scale-down.
    """

    def __init__(
        self,
        task_board: TaskBoard,
        instance_manager: InstanceManager,
        roles: dict[str, RoleConfig],
        agent_factory: Callable[[str, RoleConfig], Awaitable[asyncio.Task]] | None = None,
        agent_stopper: Callable[[str], Awaitable[None]] | None = None,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        idle_threshold_seconds: float = DEFAULT_IDLE_THRESHOLD_SECONDS,
        event_bus: EventBus | None = None,
    ) -> None:
        self._board = task_board
        self._instances = instance_manager
        self._roles = roles
        self._agent_factory = agent_factory
        self._agent_stopper = agent_stopper
        self._running = False
        self._active_extra: dict[str, int] = {}  # role -> extra instances spawned
        self._cooldown_seconds = cooldown_seconds
        self._idle_threshold_seconds = idle_threshold_seconds
        self._event_bus = event_bus
        # Timestamps of last scaling action per role, keyed by (role, direction).
        self._last_scale_at: dict[tuple[str, str], float] = {}

    async def run(self, interval: float = 15.0) -> None:
        """Main scaling loop.

        Runs indefinitely (until :meth:`stop` is called), checking queue
        depths every *interval* seconds and adjusting instance counts.
        """
        self._running = True
        while self._running:
            await asyncio.sleep(interval)
            try:
                await self._check_and_scale()
            except Exception:
                logger.exception("Auto-scaler error")

    def _is_on_cooldown(self, role_name: str, direction: str) -> bool:
        """Return True if the last scaling action for *role_name* in *direction*
        ('up' or 'down') occurred less than ``_cooldown_seconds`` ago."""
        last = self._last_scale_at.get((role_name, direction))
        if last is None:
            return False
        return (time.monotonic() - last) < self._cooldown_seconds

    def _record_scale(self, role_name: str, direction: str) -> None:
        """Record the timestamp of a scaling action."""
        self._last_scale_at[(role_name, direction)] = time.monotonic()

    @staticmethod
    def _idle_seconds(instance: dict) -> float:
        """Return how long *instance* has been idle based on its heartbeat.

        If the heartbeat is missing we fall back to ``started_at``.  Returns 0
        if the instance is not idle or the timestamp cannot be parsed.
        """
        if instance.get("status") != "idle":
            return 0.0
        ts_str = instance.get("last_heartbeat") or instance.get("started_at")
        if not ts_str:
            return 0.0
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - ts
            return max(delta.total_seconds(), 0.0)
        except (ValueError, TypeError):
            return 0.0

    async def _check_and_scale(self) -> None:
        """Check queue depths and decide scaling actions."""
        for role_name, role_cfg in self._roles.items():
            if not role_cfg.auto_scale or not role_cfg.auto_scale.enabled:
                continue

            # Count pending tasks for this role
            board = await self._board.get_board(assigned_to=role_name)
            pending_count = len(board.get("pending", []))

            # Count active instances
            instances = await self._instances.get_instances_by_role(role_name)
            active_count = len(
                [i for i in instances if i["status"] in ("idle", "working")]
            )

            threshold = role_cfg.auto_scale.scale_up_threshold
            max_instances = role_cfg.max_instances

            # Scale up: if pending > threshold and room to grow (with cooldown)
            if (
                pending_count > threshold
                and active_count < max_instances
                and not self._is_on_cooldown(role_name, "up")
            ):
                needed = min(
                    pending_count - threshold,
                    max_instances - active_count,
                )
                logger.info(
                    "Auto-scaling %s: %d pending tasks, %d active, scaling up by %d",
                    role_name,
                    pending_count,
                    active_count,
                    needed,
                )
                spawned = 0
                if self._agent_factory:
                    for j in range(needed):
                        instance_id = f"{role_name}-auto-{self._active_extra.get(role_name, 0) + j + 1}"
                        try:
                            await self._agent_factory(instance_id, role_cfg)
                            spawned += 1
                            logger.info("Auto-scaler spawned %s", instance_id)
                        except Exception:
                            logger.exception("Failed to spawn %s", instance_id)
                else:
                    logger.warning(
                        "Auto-scaler: no agent_factory configured, cannot spawn "
                        "%d instance(s) for role %s",
                        needed,
                        role_name,
                    )
                    if self._event_bus:
                        await self._event_bus.emit("autoscale.needed", {
                            "role": role_name,
                            "direction": "up",
                            "needed": needed,
                        })
                self._active_extra[role_name] = (
                    self._active_extra.get(role_name, 0) + spawned
                )
                if spawned > 0:
                    self._record_scale(role_name, "up")

            # Scale down: idle instances with no pending tasks and idle long enough
            extra = self._active_extra.get(role_name, 0)
            if extra > 0 and pending_count == 0 and not self._is_on_cooldown(role_name, "down"):
                idle_instances = [
                    i for i in instances
                    if i["status"] == "idle"
                    and self._idle_seconds(i) >= self._idle_threshold_seconds
                ]
                scale_down = min(extra, len(idle_instances))
                if scale_down > 0:
                    logger.info(
                        "Auto-scaling %s: scaling down by %d",
                        role_name,
                        scale_down,
                    )
                    stopped = 0
                    if self._agent_stopper:
                        for inst in idle_instances[:scale_down]:
                            try:
                                await self._agent_stopper(inst["instance_id"])
                                stopped += 1
                                logger.info("Auto-scaler stopped %s", inst["instance_id"])
                            except Exception:
                                logger.exception("Failed to stop %s", inst["instance_id"])
                    else:
                        logger.warning(
                            "Auto-scaler: no agent_stopper configured, cannot stop "
                            "%d instance(s) for role %s",
                            scale_down,
                            role_name,
                        )
                        if self._event_bus:
                            await self._event_bus.emit("autoscale.needed", {
                                "role": role_name,
                                "direction": "down",
                                "needed": scale_down,
                            })
                    self._active_extra[role_name] = max(0, extra - scale_down)
                    if stopped > 0:
                        self._record_scale(role_name, "down")

    def get_scaling_status(self) -> dict:
        """Return current scaling state."""
        return {
            "extra_instances": dict(self._active_extra),
            "running": self._running,
        }

    def stop(self) -> None:
        """Signal the scaling loop to exit after the current iteration."""
        self._running = False
