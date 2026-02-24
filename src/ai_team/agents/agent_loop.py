"""Agent loop: poll/claim/execute/complete cycle for independent agents."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ai_team.agents.instance_manager import InstanceManager

logger = logging.getLogger(__name__)
from ai_team.config_loader import RoleConfig
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.task_board import TaskBoard

if TYPE_CHECKING:
    from ai_team.tools.worktree_manager import WorktreeManager


class AgentLoop:
    """Continuous loop that polls for tasks, executes them via the Claude SDK,
    and hands off results.

    Parameters
    ----------
    instance_id:
        Unique identifier for this agent instance.
    role_config:
        Role configuration for this agent.
    board:
        TaskBoard for claiming and completing tasks.
    event_bus:
        EventBus for emitting lifecycle events.
    instance_manager:
        InstanceManager for status tracking.
    all_roles:
        Full mapping of role name to RoleConfig (for routing context).
    cli_path:
        Optional path to the Claude CLI binary.
    project_dir:
        Working directory for the agent.
    poll_interval:
        Seconds between poll attempts when idle.
    worktree_manager:
        Optional WorktreeManager for git worktree isolation.  When provided
        the agent runs each task in its own worktree so it never touches the
        main checkout.
    """

    def __init__(
        self,
        instance_id: str,
        role_config: RoleConfig,
        board: TaskBoard,
        event_bus: EventBus,
        instance_manager: InstanceManager,
        all_roles: dict[str, RoleConfig],
        cli_path: str | None = None,
        project_dir: str = ".",
        poll_interval: float = 5.0,
        api_url: str = "http://127.0.0.1:8420",
        worktree_manager: WorktreeManager | None = None,
    ) -> None:
        self.instance_id = instance_id
        self.role_config = role_config
        self.board = board
        self.event_bus = event_bus
        self.instance_manager = instance_manager
        self.all_roles = all_roles
        self.cli_path = cli_path
        self.project_dir = project_dir
        self.poll_interval = poll_interval
        self.api_url = api_url
        self.worktree_manager = worktree_manager
        self._running = False

    async def poll_for_task(self) -> dict | None:
        """Claim next pending task for this role."""
        return await self.board.claim_task(
            role=self.role_config.role, instance_id=self.instance_id
        )

    async def build_context(self, task: dict) -> str:
        """Build prompt context from task data and parent artifacts."""
        parts: list[str] = []
        parts.append(
            f"You are {self.role_config.display_name} (instance {self.instance_id}).\n"
        )
        parts.append("## Your Task")
        parts.append(f"**{task['id']}**: {task['title']}")
        parts.append(f"Type: {task['task_type']} | Priority: {task['priority']}")
        parts.append(f"Group: {task['group_id']}")

        if task.get("description"):
            parts.append(f"\n## Description\n{task['description']}")

        if (
            task.get("parent_id")
            and "parent_artifact" in self.role_config.context_includes
        ):
            parent = await self.board.get_task(task["parent_id"])
            if parent:
                parts.append(
                    f"\n## Parent Task ({parent['id']}): {parent['title']}"
                )

        if self.role_config.routes_to:
            parts.append("\n## When Complete")
            parts.append("Create tasks for:")
            for route in self.role_config.routes_to:
                parts.append(
                    f"- **{route.role}** (types: {', '.join(route.task_types)})"
                )

        return "\n".join(parts)

    async def execute_task(self, task: dict) -> str:
        """Run Claude SDK agent. Returns output text.

        When a ``worktree_manager`` is configured, the agent runs inside a
        per-task git worktree so it never mutates the main checkout.
        """
        from ai_team.agents.base import AgentRunner
        from ai_team.config import AgentConfig

        worktree_path: str | None = None
        branch_name: str | None = None

        if self.worktree_manager:
            branch_name = f"feat/{task['id'].lower()}"
            worktree_path = await self.worktree_manager.create_worktree(
                agent_name=self.instance_id,
                branch_name=branch_name,
            )
            logger.info(
                "Agent %s using worktree %s (branch %s)",
                self.instance_id, worktree_path, branch_name,
            )

        cwd = worktree_path or self.project_dir

        try:
            agent_config = AgentConfig(
                name=self.instance_id,
                role=self.role_config.role,
                system_prompt=self.role_config.system_prompt,
                allowed_tools=self.role_config.tools,
                model=self.role_config.model,
                cwd=cwd,
                api_url=self.api_url,
            )
            runner = AgentRunner(
                config=agent_config,
                cli_path=self.cli_path,
                event_bus=self.event_bus,
            )
            context = await self.build_context(task)

            if worktree_path:
                context += (
                    f"\n\n## Git Worktree\n"
                    f"You are working in an isolated git worktree on branch "
                    f"`{branch_name}`.  Commit your changes directly to this "
                    f"branch — do NOT create new branches or switch branches."
                )

            output = await runner.run(prompt=context, cwd=cwd)

            # Record usage from SDK
            if runner.last_usage:
                u = runner.last_usage.get("usage") or {}
                await self.board._db.record_task_usage(
                    task_id=task["id"],
                    agent_id=self.instance_id,
                    input_tokens=u.get("input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0),
                    cost_usd=runner.last_usage.get("cost_usd") or 0,
                    duration_api_ms=runner.last_usage.get("duration_api_ms", 0),
                    num_turns=runner.last_usage.get("num_turns", 0),
                )

            return output
        finally:
            if self.worktree_manager:
                try:
                    await self.worktree_manager.cleanup_worktree(self.instance_id)
                except Exception:
                    logger.warning(
                        "Failed to cleanup worktree for %s", self.instance_id,
                        exc_info=True,
                    )

    async def complete_and_handoff(self, task: dict, output: str) -> None:
        """Mark task complete and emit event."""
        await self.board.complete_task(task["id"])
        await self.event_bus.emit(
            "task.completed",
            {
                "task_id": task["id"],
                "group_id": task["group_id"],
                "agent_id": self.instance_id,
                "model": self.role_config.model,
            },
        )

    async def run_once(self) -> bool:
        """One poll/claim/execute/complete cycle. Returns True if task processed."""
        # Skip polling if role is paused
        if self.instance_manager.is_role_paused(self.role_config.role):
            current = await self.instance_manager.get_instance(self.instance_id)
            if current and current["status"] != "paused":
                await self.instance_manager.update_status(self.instance_id, "paused")
                await self.event_bus.emit("agent.status_changed", {
                    "instance_id": self.instance_id, "status": "paused", "role": self.role_config.role,
                })
            return False

        # If was paused but now resumed, set back to idle
        current = await self.instance_manager.get_instance(self.instance_id)
        if current and current["status"] == "paused":
            await self.instance_manager.update_status(self.instance_id, "idle")
            await self.event_bus.emit("agent.status_changed", {
                "instance_id": self.instance_id, "status": "idle", "role": self.role_config.role,
            })

        task = await self.poll_for_task()
        if task is None:
            return False

        logger.info("Agent %s claimed task %s: %s", self.instance_id, task["id"], task["title"])
        await self.instance_manager.update_status(
            self.instance_id, "working", current_task=task["id"]
        )
        await self.event_bus.emit(
            "task.claimed",
            {"task_id": task["id"], "claimed_by": self.instance_id, "model": self.role_config.model},
        )

        try:
            output = await self.execute_task(task)
            logger.info("Agent %s completed task %s", self.instance_id, task["id"])
            await self.complete_and_handoff(task, output)
        except Exception as e:
            logger.error("Agent %s failed task %s: %s", self.instance_id, task["id"], e, exc_info=True)
            await self.board.fail_task(task["id"])
            await self.event_bus.emit(
                "task.failed",
                {"task_id": task["id"], "error": str(e), "model": self.role_config.model},
            )
        finally:
            await self.instance_manager.update_status(self.instance_id, "idle")

        return True

    async def run(self) -> None:
        """Main continuous loop."""
        self._running = True
        await self.instance_manager.register_instance(
            self.instance_id, self.role_config
        )
        await self.event_bus.emit(
            "agent.status_changed",
            {"instance_id": self.instance_id, "status": "idle"},
        )
        logger.info("Agent %s started, polling every %ss", self.instance_id, self.poll_interval)

        while self._running:
            try:
                processed = await self.run_once()
                if not processed:
                    await asyncio.sleep(self.poll_interval)
            except Exception:
                logger.exception("Agent %s crashed in run_once, recovering", self.instance_id)
                await self.instance_manager.update_status(self.instance_id, "idle")
                await asyncio.sleep(self.poll_interval)
            await self.instance_manager.heartbeat(self.instance_id)

    def stop(self) -> None:
        """Signal the run loop to stop after the current iteration."""
        self._running = False
