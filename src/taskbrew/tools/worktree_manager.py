"""Git worktree manager for per-agent branch isolation."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# agent_name flows into worktree directory paths and is an LLM-
# controllable string in several callers. It MUST be validated
# before being joined with worktree_base.
_AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


def _validate_agent_name(agent_name: str) -> None:
    """Raise ValueError if *agent_name* is unsafe for use as a directory name."""
    if not isinstance(agent_name, str) or not _AGENT_NAME_PATTERN.match(agent_name):
        raise ValueError(
            f"Invalid agent name {agent_name!r}: must match "
            "[A-Za-z0-9_][A-Za-z0-9_.-]{0,63} (letters/digits/underscore/"
            "dot/hyphen only, no slashes, no traversal, not starting with '.' or '-')"
        )
    # Belt-and-suspenders: reject known-bad strings even though the regex
    # above already catches them.
    if agent_name in (".", "..") or "/" in agent_name or "\\" in agent_name or "\x00" in agent_name:
        raise ValueError(f"Invalid agent name {agent_name!r}")


class WorktreeManager:
    """Creates and manages git worktrees for agent isolation.

    Each agent gets its own worktree directory under ``worktree_base``.
    When an agent picks up a task, a worktree is created on a fresh branch
    derived from the task ID so the agent never touches the main checkout.
    """

    def __init__(self, repo_dir: str, worktree_base: str):
        self.repo_dir = repo_dir
        self.worktree_base = worktree_base
        # Resolve once; every path we hand back to shutil.rmtree must be a
        # descendant of this resolved path.
        self._worktree_base_resolved: Path | None = None
        self._worktrees: dict[str, str] = {}  # agent_name -> worktree path

    # ------------------------------------------------------------------
    # Path-safety helpers
    # ------------------------------------------------------------------

    def _resolved_base(self) -> Path:
        """Return the resolved worktree_base, creating it if necessary."""
        if self._worktree_base_resolved is None:
            os.makedirs(self.worktree_base, exist_ok=True)
            self._worktree_base_resolved = Path(self.worktree_base).resolve()
        return self._worktree_base_resolved

    def _safe_worktree_path(self, agent_name: str) -> Path:
        """Validate *agent_name* and return its canonical worktree path,
        guaranteed to be a direct child of the resolved worktree base.
        """
        _validate_agent_name(agent_name)
        base = self._resolved_base()
        candidate = (base / agent_name).resolve()
        if candidate.parent != base:
            raise ValueError(
                f"Resolved worktree path for {agent_name!r} escapes worktree_base"
            )
        return candidate

    def _is_under_base(self, path: str | os.PathLike) -> bool:
        """Return True iff *path* is a descendant of the resolved worktree base."""
        try:
            base = self._resolved_base()
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return False
        try:
            resolved.relative_to(base)
            return True
        except ValueError:
            return False

    def _safe_rmtree(self, path: str | os.PathLike) -> None:
        """Remove *path* only if:

        - it is a descendant of the resolved worktree base, AND
        - it is NOT a symlink (to prevent symlink-target deletion).

        Replaces raw shutil.rmtree(..., ignore_errors=True) which would
        happily follow a planted symlink out of the sandbox.
        """
        try:
            p = Path(path)
        except (TypeError, ValueError):
            return
        # Refuse symlinks outright -- deleting a symlink target is the
        # classic TOCTOU exploit.
        try:
            if p.is_symlink():
                logger.warning("Refusing to rmtree symlink %s", p)
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
                return
        except OSError:
            return
        if not self._is_under_base(p):
            logger.warning(
                "Refusing to rmtree %s: path escapes worktree_base %s",
                p, self.worktree_base,
            )
            return

        def _on_error(func, arg, excinfo):  # noqa: ARG001
            logger.debug("rmtree error on %s (ignored)", arg)

        shutil.rmtree(p, ignore_errors=False, onerror=_on_error)

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

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

    async def _list_git_worktree_paths(self) -> set[str]:
        """Return the set of worktree paths known to git (resolved)."""
        paths: set[str] = set()
        try:
            output = await self._run_git("worktree", "list", "--porcelain")
        except RuntimeError:
            return paths
        for line in output.splitlines():
            if line.startswith("worktree "):
                p = line[len("worktree "):]
                try:
                    paths.add(str(Path(p).resolve()))
                except (OSError, ValueError):
                    paths.add(p)
        return paths

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_worktree(self, agent_name: str, branch_name: str) -> str:
        """Create a git worktree for an agent. Returns the worktree path.

        Handles edge-cases from previous crashes:
        * If the worktree directory already exists it is force-removed first.
        * If the branch already exists the worktree checks it out instead of
          creating a new one (``-b``).
        * If the branch is checked out in a stale worktree belonging to a
          different agent, that stale worktree is removed first.
        """
        worktree_path_obj = self._safe_worktree_path(agent_name)
        worktree_path = str(worktree_path_obj)

        # Clean up stale worktree from a previous crash
        if worktree_path_obj.exists() or worktree_path_obj.is_symlink():
            logger.info("Removing stale worktree at %s", worktree_path)
            try:
                await self._run_git("worktree", "remove", worktree_path, "--force")
            except RuntimeError:
                self._safe_rmtree(worktree_path)
                await self._run_git("worktree", "prune")

        # Check if the branch is already checked out in another worktree.
        existing_wt = await self._find_worktree_for_branch(branch_name)
        if existing_wt and os.path.normpath(existing_wt) != os.path.normpath(worktree_path):
            logger.info(
                "Branch %s already checked out in stale worktree %s, removing it",
                branch_name, existing_wt,
            )
            try:
                await self._run_git("worktree", "remove", existing_wt, "--force")
            except RuntimeError:
                # Only rmtree if the stale worktree is inside our sandbox.
                self._safe_rmtree(existing_wt)
                await self._run_git("worktree", "prune")

        if await self._branch_exists(branch_name):
            # Branch survives from a previous run -- reuse it
            await self._run_git("worktree", "add", worktree_path, branch_name)
        else:
            # Use -- to prevent any leading-hyphen argument from being
            # parsed as a git flag (worktree path + new branch are both
            # behind the terminator).
            await self._run_git("worktree", "add", "-b", branch_name, "--", worktree_path)

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
            self._safe_rmtree(path)
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
        """Prune any leftover worktree metadata from previous crashes.

        Source of truth is ``git worktree list --porcelain``. Only directories
        that git has already forgotten AND that live inside the resolved
        worktree base are removed. This is the fix for audit 05 F#2: the
        previous implementation deleted every sibling of worktree_base that
        was not in the in-memory ``_worktrees`` dict, which on any restart
        (empty dict) deleted arbitrary user data.
        """
        try:
            await self._run_git("worktree", "prune")
        except RuntimeError:
            pass

        base = self._resolved_base()
        if not base.is_dir():
            return

        git_known_paths = await self._list_git_worktree_paths()
        in_memory_resolved = set()
        for p in self._worktrees.values():
            try:
                in_memory_resolved.add(str(Path(p).resolve()))
            except (OSError, ValueError):
                pass

        for entry in base.iterdir():
            # Refuse to touch anything that isn't a plain directory
            # (don't follow symlinks out of the sandbox).
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                resolved_entry = str(entry.resolve())
            except (OSError, ValueError):
                continue
            # Skip entries still tracked by git (live worktrees from any
            # process, not just this one). This is the critical invariant
            # we lost previously.
            if resolved_entry in git_known_paths:
                continue
            # Skip entries currently owned by this process.
            if entry.name in self._worktrees or resolved_entry in in_memory_resolved:
                continue
            logger.info("Pruning stale worktree directory %s", entry)
            self._safe_rmtree(entry)

        try:
            await self._run_git("worktree", "prune")
        except RuntimeError:
            pass
