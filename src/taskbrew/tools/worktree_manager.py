"""Git worktree manager for per-agent branch isolation."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil

logger = logging.getLogger(__name__)


class WorktreeManager:
    """Creates and manages git worktrees for agent isolation.

    Each agent gets its own worktree directory under ``worktree_base``.
    When an agent picks up a task, a worktree is created on a fresh branch
    derived from the task ID so the agent never touches the main checkout.
    """

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

    async def _branch_exists(self, branch_name: str) -> bool:
        """Check whether a local branch already exists."""
        try:
            await self._run_git("rev-parse", "--verify", f"refs/heads/{branch_name}")
            return True
        except RuntimeError:
            return False

    async def _find_worktree_for_branch(self, branch_name: str) -> str | None:
        """Return the worktree path that has *branch_name* checked out, or None."""
        try:
            output = await self._run_git("worktree", "list", "--porcelain")
        except RuntimeError:
            return None

        current_path: str | None = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                current_path = line[len("worktree "):]
            elif line.startswith("branch refs/heads/"):
                checked_out_branch = line[len("branch refs/heads/"):]
                if checked_out_branch == branch_name and current_path:
                    return current_path
        return None

    async def create_worktree(self, agent_name: str, branch_name: str) -> str:
        """Create a git worktree for an agent. Returns the worktree path.

        Handles edge-cases from previous crashes:
        * If the worktree directory already exists it is force-removed first.
        * If the branch already exists the worktree checks it out instead of
          creating a new one (``-b``).
        * If the branch is checked out in a stale worktree belonging to a
          different agent, that stale worktree is removed first.
        """
        os.makedirs(self.worktree_base, exist_ok=True)
        worktree_path = os.path.join(self.worktree_base, agent_name)

        # Clean up stale worktree from a previous crash
        if os.path.exists(worktree_path):
            logger.info("Removing stale worktree at %s", worktree_path)
            try:
                await self._run_git("worktree", "remove", worktree_path, "--force")
            except RuntimeError:
                # If git can't remove it, nuke the directory and prune
                shutil.rmtree(worktree_path, ignore_errors=True)
                await self._run_git("worktree", "prune")

        # Check if the branch is already checked out in another worktree.
        # This can happen when a recovered task gets claimed by a different
        # agent while the old agent's stale worktree still has the branch.
        existing_wt = await self._find_worktree_for_branch(branch_name)
        if existing_wt and os.path.normpath(existing_wt) != os.path.normpath(worktree_path):
            logger.info(
                "Branch %s already checked out in stale worktree %s, removing it",
                branch_name, existing_wt,
            )
            try:
                await self._run_git("worktree", "remove", existing_wt, "--force")
            except RuntimeError:
                shutil.rmtree(existing_wt, ignore_errors=True)
                await self._run_git("worktree", "prune")

        if await self._branch_exists(branch_name):
            # Branch survives from a previous run — reuse it
            await self._run_git("worktree", "add", worktree_path, branch_name)
        else:
            await self._run_git("worktree", "add", worktree_path, "-b", branch_name)

        self._worktrees[agent_name] = worktree_path
        return worktree_path

    async def cleanup_worktree(self, agent_name: str) -> None:
        """Remove an agent's worktree (keeps the branch and its commits)."""
        path = self._worktrees.pop(agent_name, None)
        if not path:
            return
        try:
            await self._run_git("worktree", "remove", path, "--force")
        except RuntimeError:
            shutil.rmtree(path, ignore_errors=True)
            try:
                await self._run_git("worktree", "prune")
            except RuntimeError:
                pass

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

    async def prune_stale(self) -> None:
        """Prune any leftover worktree metadata from previous crashes."""
        try:
            await self._run_git("worktree", "prune")
        except RuntimeError:
            pass
        # Also clean up any leftover directories
        if os.path.isdir(self.worktree_base):
            for entry in os.listdir(self.worktree_base):
                full = os.path.join(self.worktree_base, entry)
                if os.path.isdir(full) and entry not in self._worktrees:
                    logger.info("Pruning stale worktree directory %s", full)
                    shutil.rmtree(full, ignore_errors=True)
            try:
                await self._run_git("worktree", "prune")
            except RuntimeError:
                pass
