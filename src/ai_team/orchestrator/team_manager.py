"""Manages the lifecycle of agent instances."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ai_team.agents.base import AgentRunner, AgentStatus
from ai_team.agents.roles import get_agent_config, AGENT_ROLES
from ai_team.orchestrator.event_bus import EventBus

if TYPE_CHECKING:
    from ai_team.tools.worktree_manager import WorktreeManager


class TeamManager:
    """Spawns, stops, and monitors agent instances."""

    def __init__(
        self,
        event_bus: EventBus,
        cli_path: str | None = None,
        worktree_manager: WorktreeManager | None = None,
        max_concurrent_agents: int = 3,
    ):
        self.event_bus = event_bus
        self.cli_path = cli_path
        self.worktree_manager = worktree_manager
        self.agents: dict[str, AgentRunner] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent_agents)

    def spawn_agent(self, role_name: str) -> AgentRunner:
        if role_name in self.agents:
            raise ValueError(f"Agent '{role_name}' already exists")
        config = get_agent_config(role_name)
        runner = AgentRunner(config, cli_path=self.cli_path)
        self.agents[role_name] = runner
        return runner

    def stop_agent(self, agent_name: str) -> None:
        if agent_name in self.agents:
            self.agents[agent_name].status = AgentStatus.STOPPED

    def get_agent(self, agent_name: str) -> AgentRunner | None:
        return self.agents.get(agent_name)

    def get_team_status(self) -> dict[str, AgentStatus]:
        return {name: agent.status for name, agent in self.agents.items()}

    def spawn_default_team(self) -> None:
        for role_name in AGENT_ROLES:
            if role_name not in self.agents:
                self.spawn_agent(role_name)

    async def run_agent_task(self, agent_name: str, prompt: str, cwd: str | None = None) -> str:
        agent = self.agents.get(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' not found")
        # Use worktree path if available
        effective_cwd = cwd
        if self.worktree_manager:
            worktree_path = self.worktree_manager.get_worktree_path(agent_name)
            if worktree_path:
                effective_cwd = worktree_path
        await self.event_bus.emit("agent_started", {"agent": agent_name, "prompt": prompt[:200]})
        try:
            result = await agent.run(prompt, cwd=effective_cwd)
            await self.event_bus.emit(
                "agent_completed", {"agent": agent_name, "result": result[:500]}
            )
            return result
        except Exception as e:
            await self.event_bus.emit("agent_error", {"agent": agent_name, "error": str(e)})
            raise

    async def run_agents_concurrent(self, tasks: list[dict]) -> dict[str, str]:
        """Run multiple agent tasks concurrently, bounded by semaphore.

        Args:
            tasks: list of {"agent": name, "prompt": str, "cwd": str}

        Returns:
            dict of agent_name -> result (or exception)
        """

        async def _run_with_semaphore(task):
            async with self._semaphore:
                return await self.run_agent_task(task["agent"], task["prompt"], task.get("cwd"))

        results = await asyncio.gather(
            *[_run_with_semaphore(t) for t in tasks],
            return_exceptions=True,
        )
        return {t["agent"]: r for t, r in zip(tasks, results)}
