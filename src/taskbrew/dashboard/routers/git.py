"""Git integration routes: branch info, recent commits, diff summary, and file changes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from taskbrew.dashboard.routers._deps import get_orch_optional

router = APIRouter()
logger = logging.getLogger(__name__)


async def _run_git(
    *args: str, cwd: str | None = None, timeout: float = 10.0
) -> tuple[str, str, int]:
    """Run a git command and return (stdout, stderr, returncode)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Git command timed out")
    return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode


def _get_project_dir() -> str | None:
    """Get the project directory from the orchestrator."""
    orch = get_orch_optional()
    if orch and hasattr(orch, "project_dir") and orch.project_dir:
        return orch.project_dir
    return None


# ------------------------------------------------------------------
# Git status
# ------------------------------------------------------------------


@router.get("/api/git/status")
async def git_status():
    """Get git working tree status."""
    cwd = _get_project_dir()
    stdout, stderr, rc = await _run_git("status", "--porcelain", "-b", cwd=cwd)
    if rc != 0:
        raise HTTPException(500, f"git status failed: {stderr.strip()}")

    lines = stdout.strip().split("\n") if stdout.strip() else []
    branch_line = lines[0] if lines and lines[0].startswith("##") else None
    file_lines = [line for line in lines if not line.startswith("##")]

    branch = None
    tracking = None
    if branch_line:
        parts = branch_line[3:].split("...")
        branch = parts[0]
        if len(parts) > 1:
            tracking = parts[1].split()[0] if parts[1] else None

    changes = []
    for line in file_lines:
        if len(line) >= 3:
            status_code = line[:2]
            filepath = line[3:]
            changes.append({"status": status_code.strip(), "file": filepath})

    return {
        "branch": branch,
        "tracking": tracking,
        "clean": len(changes) == 0,
        "changes": changes,
        "total_changes": len(changes),
    }


# ------------------------------------------------------------------
# Recent commits
# ------------------------------------------------------------------


@router.get("/api/git/log")
async def git_log(limit: int = Query(20, ge=1, le=100)):
    """Get recent commit log."""
    cwd = _get_project_dir()
    fmt = "%H%n%h%n%an%n%ae%n%ai%n%s"
    stdout, stderr, rc = await _run_git(
        "log", f"--max-count={limit}", f"--format={fmt}", cwd=cwd
    )
    if rc != 0:
        raise HTTPException(500, f"git log failed: {stderr.strip()}")

    commits = []
    lines = stdout.strip().split("\n") if stdout.strip() else []
    # Each commit is 6 lines
    for i in range(0, len(lines), 6):
        if i + 5 < len(lines):
            commits.append({
                "hash": lines[i],
                "short_hash": lines[i + 1],
                "author": lines[i + 2],
                "email": lines[i + 3],
                "date": lines[i + 4],
                "message": lines[i + 5],
            })

    return {"commits": commits, "count": len(commits)}


# ------------------------------------------------------------------
# Branches
# ------------------------------------------------------------------


@router.get("/api/git/branches")
async def git_branches():
    """List all local branches with current branch indicated."""
    cwd = _get_project_dir()
    stdout, stderr, rc = await _run_git("branch", "--list", "-v", cwd=cwd)
    if rc != 0:
        raise HTTPException(500, f"git branch failed: {stderr.strip()}")

    branches = []
    current = None
    for line in stdout.strip().split("\n"):
        if not line.strip():
            continue
        is_current = line.startswith("*")
        name = line.lstrip("* ").split()[0]
        if is_current:
            current = name
        branches.append({"name": name, "current": is_current})

    return {"current": current, "branches": branches, "count": len(branches)}


# ------------------------------------------------------------------
# Diff summary
# ------------------------------------------------------------------


@router.get("/api/git/diff")
async def git_diff(staged: bool = Query(False)):
    """Get diff summary (--stat). Use staged=true for staged changes."""
    cwd = _get_project_dir()
    args = ["diff", "--stat"]
    if staged:
        args.append("--cached")
    stdout, stderr, rc = await _run_git(*args, cwd=cwd)
    if rc != 0:
        raise HTTPException(500, f"git diff failed: {stderr.strip()}")

    return {"staged": staged, "summary": stdout.strip()}


# ------------------------------------------------------------------
# File diff
# ------------------------------------------------------------------


@router.get("/api/git/diff/{file_path:path}")
async def git_file_diff(file_path: str):
    """Get diff for a specific file."""
    cwd = _get_project_dir()
    stdout, stderr, rc = await _run_git("diff", "--", file_path, cwd=cwd)
    if rc != 0:
        raise HTTPException(500, f"git diff failed: {stderr.strip()}")

    return {"file": file_path, "diff": stdout}


# ------------------------------------------------------------------
# Stash list
# ------------------------------------------------------------------


@router.get("/api/git/stash")
async def git_stash_list():
    """List git stashes."""
    cwd = _get_project_dir()
    stdout, stderr, rc = await _run_git("stash", "list", cwd=cwd)
    if rc != 0:
        raise HTTPException(500, f"git stash list failed: {stderr.strip()}")

    stashes = []
    for line in stdout.strip().split("\n"):
        if line.strip():
            stashes.append(line.strip())

    return {"stashes": stashes, "count": len(stashes)}


# ------------------------------------------------------------------
# Tags
# ------------------------------------------------------------------


@router.get("/api/git/tags")
async def git_tags(limit: int = Query(20, ge=1, le=100)):
    """List recent tags."""
    cwd = _get_project_dir()
    stdout, stderr, rc = await _run_git(
        "tag", "--sort=-creatordate", cwd=cwd
    )
    if rc != 0:
        raise HTTPException(500, f"git tag failed: {stderr.strip()}")

    tags = [t.strip() for t in stdout.strip().split("\n") if t.strip()][:limit]
    return {"tags": tags, "count": len(tags)}
