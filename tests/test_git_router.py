"""Tests for the git integration dashboard router."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager


@pytest.fixture
async def app_client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
        project_dir=str(tmp_path),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "tmp_path": tmp_path}
    await db.close()


def _mock_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock asyncio subprocess."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


class TestGitStatus:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_status_clean(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("## main...origin/main\n")
        resp = await app_client["client"].get("/api/git/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["branch"] == "main"
        assert data["clean"] is True
        assert data["total_changes"] == 0

    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_status_with_changes(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process(
            "## feat/test...origin/feat/test\n"
            " M src/main.py\n"
            "?? new_file.txt\n"
        )
        resp = await app_client["client"].get("/api/git/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["branch"] == "feat/test"
        assert data["clean"] is False
        assert data["total_changes"] == 2
        assert data["changes"][0]["file"] == "src/main.py"

    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_status_error(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process(stderr="fatal: not a git repo", returncode=128)
        resp = await app_client["client"].get("/api/git/status")
        assert resp.status_code == 500


class TestGitLog:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_log_with_commits(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process(
            "abc123def\nabc123d\nJohn Doe\njohn@example.com\n2026-02-26 12:00:00 +0000\nInitial commit\n"
            "def456abc\ndef456a\nJane Doe\njane@example.com\n2026-02-25 12:00:00 +0000\nAdd tests\n"
        )
        resp = await app_client["client"].get("/api/git/log?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["commits"][0]["message"] == "Initial commit"
        assert data["commits"][1]["author"] == "Jane Doe"

    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_log_empty(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("")
        resp = await app_client["client"].get("/api/git/log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0


class TestGitBranches:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_branches(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process(
            "* main    abc1234 Latest commit\n"
            "  develop def5678 Dev commit\n"
            "  feature 789abcd Feature work\n"
        )
        resp = await app_client["client"].get("/api/git/branches")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == "main"
        assert data["count"] == 3
        assert data["branches"][0]["current"] is True


class TestGitDiff:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_diff_unstaged(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process(" src/main.py | 3 +++\n 1 file changed\n")
        resp = await app_client["client"].get("/api/git/diff")
        assert resp.status_code == 200
        data = resp.json()
        assert data["staged"] is False
        assert "main.py" in data["summary"]

    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_diff_staged(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("no changes\n")
        resp = await app_client["client"].get("/api/git/diff?staged=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["staged"] is True


class TestGitFileDiff:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_file_diff(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("diff --git a/foo.py b/foo.py\n+new line\n")
        resp = await app_client["client"].get("/api/git/diff/foo.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["file"] == "foo.py"
        assert "+new line" in data["diff"]


class TestGitStash:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_stash_list(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("stash@{0}: WIP on main\nstash@{1}: On feature\n")
        resp = await app_client["client"].get("/api/git/stash")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2

    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_stash_empty(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("")
        resp = await app_client["client"].get("/api/git/stash")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0


class TestGitTags:
    @patch("taskbrew.dashboard.routers.git.asyncio.create_subprocess_exec")
    async def test_tags(self, mock_exec, app_client):
        mock_exec.return_value = _mock_process("v1.0.0\nv0.9.0\n")
        resp = await app_client["client"].get("/api/git/tags")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert "v1.0.0" in data["tags"]
