"""Agent loop: poll/claim/execute/complete cycle for independent agents."""

from __future__ import annotations

import asyncio

from ai_team.agents.instance_manager import InstanceManager
from ai_team.config_loader import RoleConfig
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.task_board import TaskBoard


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
        """Run Claude SDK agent. Returns output text."""
        from ai_team.agents.base import AgentRunner
        from ai_team.config import AgentConfig

        agent_config = AgentConfig(
            name=self.instance_id,
            role=self.role_config.role,
            system_prompt=self.role_config.system_prompt,
            allowed_tools=self.role_config.tools,
            cwd=self.project_dir,
        )
        runner = AgentRunner(
            config=agent_config,
            cli_path=self.cli_path,
            event_bus=self.event_bus,
        )
        context = await self.build_context(task)
        return await runner.run(prompt=context, cwd=self.project_dir)

    async def complete_and_handoff(self, task: dict, output: str) -> None:
        """Mark task complete and emit event."""
        await self.board.complete_task(task["id"])
        await self.event_bus.emit(
            "task.completed",
            {
                "task_id": task["id"],
                "group_id": task["group_id"],
                "agent_id": self.instance_id,
            },
        )

    async def run_once(self) -> bool:
        """One poll/claim/execute/complete cycle. Returns True if task processed."""
        task = await self.poll_for_task()
        if task is None:
            return False

        await self.instance_manager.update_status(
            self.instance_id, "working", current_task=task["id"]
        )
        await self.event_bus.emit(
            "task.claimed",
            {"task_id": task["id"], "claimed_by": self.instance_id},
        )

        try:
            output = await self.execute_task(task)
            await self.complete_and_handoff(task, output)
        except Exception as e:
            await self.board.fail_task(task["id"])
            await self.event_bus.emit(
                "task.failed",
                {"task_id": task["id"], "error": str(e)},
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

        while self._running:
            processed = await self.run_once()
            if not processed:
                await asyncio.sleep(self.poll_interval)
            await self.instance_manager.heartbeat(self.instance_id)

    def stop(self) -> None:
        """Signal the run loop to stop after the current iteration."""
        self._running = False
