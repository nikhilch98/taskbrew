import pytest
import subprocess
import os
from ai_team.tools.worktree_manager import WorktreeManager


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo for testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True, check=True)
    (repo / "README.md").write_text("# Test")
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
