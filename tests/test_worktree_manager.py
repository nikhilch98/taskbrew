import pytest
import subprocess
import os
from taskbrew.tools.worktree_manager import WorktreeManager


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Force main as the initial branch so tests don't depend on the
    # operator's git init.defaultBranch config.
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=str(repo), capture_output=True, check=True,
    )
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True, check=True)
    (repo / "README.md").write_text("# Test")
    # A .gitignore so tests can verify that ignored files survive a
    # cross-task worktree reuse.
    (repo / ".gitignore").write_text("node_modules/\n.venv/\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), capture_output=True, check=True)
    return repo


@pytest.fixture
def worktree_manager(git_repo):
    return WorktreeManager(repo_dir=str(git_repo), worktree_base=str(git_repo / ".worktrees"))


async def test_create_worktree(worktree_manager):
    path = await worktree_manager.create_worktree("coder", "feature/test")
    assert os.path.isdir(path)
    assert os.path.exists(os.path.join(path, "README.md"))


async def test_list_worktrees(worktree_manager):
    await worktree_manager.create_worktree("coder", "feature/a")
    await worktree_manager.create_worktree("tester", "feature/b")
    trees = await worktree_manager.list_worktrees()
    assert len(trees) == 2


async def test_cleanup_worktree(worktree_manager):
    path = await worktree_manager.create_worktree("coder", "feature/cleanup")
    assert os.path.isdir(path)
    await worktree_manager.cleanup_worktree("coder")
    assert not os.path.isdir(path)


async def test_get_worktree_path(worktree_manager):
    await worktree_manager.create_worktree("coder", "feature/path")
    path = worktree_manager.get_worktree_path("coder")
    assert path is not None
    assert "coder" in path


async def test_get_worktree_path_returns_none_for_unknown(worktree_manager):
    path = worktree_manager.get_worktree_path("unknown")
    assert path is None


async def test_stale_worktree_is_replaced(worktree_manager):
    """If a worktree directory already exists (crash recovery), re-create it."""
    path = await worktree_manager.create_worktree("coder", "feature/stale")
    assert os.path.isdir(path)
    # Simulate a crash: remove from tracking but leave directory
    worktree_manager._worktrees.clear()
    # Creating with same agent name but different branch should work
    path2 = await worktree_manager.create_worktree("coder", "feature/stale-2")
    assert os.path.isdir(path2)
    assert path2 == path  # same directory slot


async def test_existing_branch_reuse(worktree_manager):
    """If branch already exists, reuse it instead of -b."""
    path = await worktree_manager.create_worktree("coder", "feature/reuse")
    assert os.path.isdir(path)
    await worktree_manager.cleanup_worktree("coder")
    assert not os.path.isdir(path)
    # Branch still exists after cleanup, create worktree again
    path2 = await worktree_manager.create_worktree("coder", "feature/reuse")
    assert os.path.isdir(path2)


async def test_prune_stale(worktree_manager, git_repo):
    """prune_stale should clean up leftover directories."""
    wt_base = git_repo / ".worktrees"
    wt_base.mkdir(exist_ok=True)
    stale_dir = wt_base / "ghost-agent"
    stale_dir.mkdir()
    (stale_dir / "junk.txt").write_text("leftover")
    await worktree_manager.prune_stale()
    assert not stale_dir.exists()


async def test_cleanup_all(worktree_manager):
    """cleanup_all removes all managed worktrees."""
    await worktree_manager.create_worktree("a", "feature/a")
    await worktree_manager.create_worktree("b", "feature/b")
    assert len(await worktree_manager.list_worktrees()) == 2
    await worktree_manager.cleanup_all()
    assert len(await worktree_manager.list_worktrees()) == 0


# ------------------------------------------------------------------
# Cross-task worktree reuse
# docs/superpowers/specs/2026-04-24-worktree-reuse-across-tasks-design.md
# ------------------------------------------------------------------


async def test_worktree_reuse_preserves_ignored_files(worktree_manager):
    """Dropping node_modules/ (ignored) between two create_worktree
    calls for the same agent must survive the branch switch.
    This is the whole point of cross-task reuse."""
    path = await worktree_manager.create_worktree(
        "coder-1", "feat/a", base_branch="main",
    )
    # Simulate npm install: create an ignored directory with content.
    node_modules = os.path.join(path, "node_modules")
    os.makedirs(node_modules)
    marker = os.path.join(node_modules, "marker.txt")
    with open(marker, "w") as fh:
        fh.write("deps-are-installed")

    # Switch to a different branch — same agent, different task.
    path_again = await worktree_manager.create_worktree(
        "coder-1", "feat/b", base_branch="main",
    )
    assert path_again == path, "reuse should return the same path"
    assert os.path.exists(marker), (
        "ignored node_modules must survive the branch switch"
    )


async def test_worktree_reuse_resets_tracked_uncommitted_changes(worktree_manager):
    """Tracked files with uncommitted changes from a prior task must
    NOT leak into the next task — `git reset --hard` scrubs them."""
    path = await worktree_manager.create_worktree(
        "coder-1", "feat/a", base_branch="main",
    )
    # Modify the tracked README.md without committing.
    with open(os.path.join(path, "README.md"), "w") as fh:
        fh.write("# MODIFIED BUT UNCOMMITTED")

    await worktree_manager.create_worktree(
        "coder-1", "feat/b", base_branch="main",
    )
    with open(os.path.join(path, "README.md")) as fh:
        content = fh.read()
    assert "MODIFIED BUT UNCOMMITTED" not in content
    assert "# Test" in content


async def test_worktree_reuse_same_branch_is_noop(worktree_manager):
    """Calling create_worktree twice with the same branch for the same
    agent returns the same path without destructive operations."""
    path1 = await worktree_manager.create_worktree(
        "coder-1", "feat/a", base_branch="main",
    )
    # Drop a marker that would NOT survive a tear-down-rebuild.
    marker = os.path.join(path1, "node_modules", "marker.txt")
    os.makedirs(os.path.dirname(marker))
    with open(marker, "w") as fh:
        fh.write("x")

    path2 = await worktree_manager.create_worktree(
        "coder-1", "feat/a", base_branch="main",
    )
    assert path1 == path2
    assert os.path.exists(marker), (
        "same-branch reuse should not touch untracked files"
    )


async def test_worktree_separate_agents_get_separate_worktrees(worktree_manager):
    """Regression: reuse is scoped per-agent. Different agents get
    different worktrees even if they ask for the same branch."""
    path_a = await worktree_manager.create_worktree(
        "coder-1", "feat/shared", base_branch="main",
    )
    # coder-2 asking for the same branch would normally conflict, so
    # give it a different branch.
    path_b = await worktree_manager.create_worktree(
        "coder-2", "feat/other", base_branch="main",
    )
    assert path_a != path_b
    assert os.path.exists(path_a)
    assert os.path.exists(path_b)


async def test_cleanup_removes_reused_worktree(worktree_manager):
    """After cross-task reuse, cleanup_worktree still destroys the
    worktree correctly."""
    path = await worktree_manager.create_worktree(
        "coder-1", "feat/a", base_branch="main",
    )
    await worktree_manager.create_worktree(
        "coder-1", "feat/b", base_branch="main",
    )  # reuse
    assert os.path.exists(path)
    await worktree_manager.cleanup_worktree("coder-1")
    assert not os.path.exists(path)
