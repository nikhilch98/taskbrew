"""Git worktree manager for per-agent branch isolation."""

import asyncio
import os
import shutil


class WorktreeManager:
    """Creates and manages git worktrees for agent isolation."""

    def __init__(self, repo_dir: str, worktree_base: str):
        self.repo_dir = repo_dir
        self.worktree_base = worktree_base
        self._worktrees: dict[str, str] = {}  # agent_name -> worktree path

    async def _run_git(self, *args: str, cwd: str | None = None) -> str:
        """Run a git command and return stdout."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=cwd or self.repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode()}")
        return stdout.decode().strip()

    async def create_worktree(self, agent_name: str, branch_name: str) -> str:
        """Create a git worktree for an agent. Returns the worktree path."""
        os.makedirs(self.worktree_base, exist_ok=True)
        worktree_path = os.path.join(self.worktree_base, agent_name)
        await self._run_git("worktree", "add", worktree_path, "-b", branch_name)
        self._worktrees[agent_name] = worktree_path
        return worktree_path

    async def cleanup_worktree(self, agent_name: str) -> None:
        """Remove an agent's worktree."""
        path = self._worktrees.get(agent_name)
        if not path:
            return
        await self._run_git("worktree", "remove", path, "--force")
        del self._worktrees[agent_name]

    async def list_worktrees(self) -> list[dict]:
        """List all managed worktrees."""
        return [
            {"agent": name, "path": path}
            for name, path in self._worktrees.items()
        ]

    def get_worktree_path(self, agent_name: str) -> str | None:
        """Get the worktree path for an agent."""
        return self._worktrees.get(agent_name)

    async def cleanup_all(self) -> None:
        """Remove all managed worktrees."""
        for agent_name in list(self._worktrees):
            await self.cleanup_worktree(agent_name)
