"""Tests for durable merge queue and broker behavior."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.merge_broker import MergeBroker
from taskbrew.orchestrator.merge_queue import MergeQueue
from taskbrew.orchestrator.task_board import TaskBoard


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def board(db: Database) -> TaskBoard:
    tb = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await tb.register_prefixes({"pm": "PM", "coder": "CD", "verifier": "VR"})
    return tb


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# Test\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    try:
        _git(repo, "branch", "-M", "main")
    except subprocess.CalledProcessError:
        pass


async def _verified_task_pair(board: TaskBoard, branch_name: str = "feat/cd-001"):
    group = await board.create_group(title="Feature", origin="human", created_by="human")
    parent = await board.create_task(
        group_id=group["id"], title="Implementation",
        task_type="implementation", assigned_to="coder",
        created_by="architect-1", branch_name=branch_name, parent_branch="main",
    )
    verifier = await board.create_task(
        group_id=group["id"], title="Verify",
        task_type="verification", assigned_to="verifier",
        created_by="coder-1", parent_id=parent["id"],
    )
    return group, parent, verifier


async def test_merge_queue_enqueue_is_idempotent(board: TaskBoard):
    group, parent, verifier = await _verified_task_pair(board)
    queue = MergeQueue(board._db)

    first = await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )
    second = await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )

    assert first["id"] == second["id"]
    counts = await queue.counts_for_group(group["id"])
    assert counts == {"queued": 1}


async def test_merge_queue_claims_fifo_and_recovers_expired_leases(board: TaskBoard):
    group, parent, verifier = await _verified_task_pair(board, "feat/cd-001")
    _, parent2, verifier2 = await _verified_task_pair(board, "feat/cd-002")
    queue = MergeQueue(board._db)
    first = await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )
    await queue.enqueue(
        group_id=group["id"], parent_task_id=parent2["id"],
        verifier_task_id=verifier2["id"], source_branch="feat/cd-002",
        target_branch="main",
    )

    claimed = await queue.claim_ready(worker_id="broker", lease_seconds=0)
    assert claimed["id"] == first["id"]
    assert await queue.release_expired_leases() == 1
    reclaimed = await queue.claim_ready(worker_id="broker", lease_seconds=10)
    assert reclaimed["id"] == first["id"]


async def test_group_completion_blocks_on_open_merge_queue(board: TaskBoard):
    group, parent, verifier = await _verified_task_pair(board)
    queue = MergeQueue(board._db)
    await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'completed' WHERE group_id = ?",
        (group["id"],),
    )

    await board._check_group_completion(parent["id"])
    row = await board._db.execute_fetchone(
        "SELECT status FROM groups WHERE id = ?", (group["id"],),
    )
    assert row["status"] == "active"

    await queue.complete(first_row_id := (await queue.claim_ready(worker_id="b"))["id"], status="merged")
    assert first_row_id
    await board._check_group_completion(parent["id"])
    row = await board._db.execute_fetchone(
        "SELECT status FROM groups WHERE id = ?", (group["id"],),
    )
    assert row["status"] == "completed"


async def test_successful_merge_supersedes_older_conflict_for_parent(board: TaskBoard):
    group, parent, verifier = await _verified_task_pair(board, "feat/cd-001")
    _, _, verifier2 = await _verified_task_pair(board, "feat/cd-001-revision")
    queue = MergeQueue(board._db)
    old = await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )
    newer = await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier2["id"], source_branch="feat/cd-001-revision",
        target_branch="main",
    )
    await queue.complete(old["id"], status="conflict", details="CONFLICT")
    await queue.complete(newer["id"], status="merged")

    count = await queue.supersede_open_for_parent(
        parent_task_id=parent["id"],
        except_row_id=newer["id"],
        details="newer merge landed",
    )

    assert count == 1
    rows = await board._db.execute_fetchall("SELECT id, status FROM merge_queue")
    statuses = {row["id"]: row["status"] for row in rows}
    assert statuses == {
        old["id"]: "superseded",
        newer["id"]: "merged",
    }
    assert await queue.has_open_group_merges(group["id"]) is False


async def test_merge_broker_merges_branch_and_refreshes_root(tmp_path, board: TaskBoard):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "add_numbers.py").write_text("print(1 + 2)\n")
    _git(repo, "add", "add_numbers.py")
    _git(repo, "commit", "-m", "add script")
    _git(repo, "checkout", "main")

    group, parent, verifier = await _verified_task_pair(board)
    queue = MergeQueue(board._db)
    await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )
    broker = MergeBroker(
        merge_queue=queue, task_board=board, event_bus=EventBus(),
        repo_dir=str(repo), poll_interval=0.01,
    )

    assert await broker.process_once() is True

    assert (repo / "add_numbers.py").read_text() == "print(1 + 2)\n"
    assert _git(repo, "branch", "--contains", "feat/cd-001").splitlines()
    parent_row = await board.get_task(parent["id"])
    verifier_row = await board.get_task(verifier["id"])
    counts = await queue.counts_for_group(group["id"])
    assert parent_row["merge_status"] == "merged"
    assert verifier_row["merge_status"] == "merged"
    assert counts == {"merged": 1}


async def test_merge_broker_preserves_dirty_root_changes(tmp_path, board: TaskBoard):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "add_numbers.py").write_text("print(1 + 2)\n")
    _git(repo, "add", "add_numbers.py")
    _git(repo, "commit", "-m", "add script")
    _git(repo, "checkout", "main")
    (repo / "README.md").write_text("# Test\n\nUser note\n")

    group, parent, verifier = await _verified_task_pair(board)
    queue = MergeQueue(board._db)
    await queue.enqueue(
        group_id=group["id"], parent_task_id=parent["id"],
        verifier_task_id=verifier["id"], source_branch="feat/cd-001",
        target_branch="main",
    )
    broker = MergeBroker(
        merge_queue=queue, task_board=board, event_bus=EventBus(),
        repo_dir=str(repo), poll_interval=0.01,
    )

    assert await broker.process_once() is True

    assert (repo / "add_numbers.py").read_text() == "print(1 + 2)\n"
    assert (repo / "README.md").read_text() == "# Test\n\nUser note\n"
    row = await board._db.execute_fetchone("SELECT root_refresh_status FROM merge_queue")
    assert row["root_refresh_status"] == "refreshed_with_stash"


async def test_merge_broker_removes_stale_repo_lock(tmp_path, board: TaskBoard):
    repo = tmp_path / "repo"
    _init_repo(repo)
    queue = MergeQueue(board._db)
    broker = MergeBroker(
        merge_queue=queue, task_board=board, event_bus=EventBus(),
        repo_dir=str(repo),
    )
    lock_path = repo / ".taskbrew" / "locks" / "integration.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("999999 test-worker 2026-01-01T00:00:00+00:00\n")
    old = time.time() - 300
    os.utime(lock_path, (old, old))

    with broker._repo_lock():
        assert lock_path.exists()
