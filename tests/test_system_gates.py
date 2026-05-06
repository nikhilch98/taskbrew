"""Tests for the async system gate manager."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.merge_queue import MergeQueue
from taskbrew.orchestrator.system_gates import (
    AgentRunnerSystemGateAnalyzer,
    BacklogIntakeResult,
    RevisionRequest,
    ReviewResult,
    SystemGateAnalysisError,
    SystemGateAnalyzer,
    SystemGateManager,
    _extract_json_object,
)
from taskbrew.orchestrator.task_board import TaskBoard


class RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class FakeAnalyzer(SystemGateAnalyzer):
    def __init__(
        self,
        *,
        backlog_results: list[BacklogIntakeResult] | None = None,
        review_results: list[ReviewResult] | None = None,
    ) -> None:
        self.backlog_results = list(backlog_results or [])
        self.review_results = list(review_results or [])
        self.backlog_contexts: list[dict] = []
        self.review_contexts: list[dict] = []

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        self.backlog_contexts.append(context)
        result = self.backlog_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def review_completed_task(self, context: dict) -> ReviewResult:
        self.review_contexts.append(context)
        result = self.review_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class FakeRunner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    async def run(self, *, prompt: str, cwd: str | None = None) -> str:
        self.calls.append({"prompt": prompt, "cwd": cwd})
        return self.text


class FakeSystemGateManager:
    def __init__(self) -> None:
        self.run_count = 0
        self.release = asyncio.Event()

    async def run(self) -> None:
        self.run_count += 1
        await self.release.wait()


class BlockingReviewAnalyzer(SystemGateAnalyzer):
    def __init__(self) -> None:
        self.entered = 0
        self.first_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        raise AssertionError("backlog analysis should not run")

    async def review_completed_task(self, context: dict) -> ReviewResult:
        self.entered += 1
        self.first_entered.set()
        await self.release.wait()
        return ReviewResult(outcome="needs_revision", reason="Add the missing test.")


class TwoCallerReviewAnalyzer(SystemGateAnalyzer):
    def __init__(self) -> None:
        self.entered = 0
        self.both_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        raise AssertionError("backlog analysis should not run")

    async def review_completed_task(self, context: dict) -> ReviewResult:
        self.entered += 1
        if self.entered == 2:
            self.both_entered.set()
        await self.release.wait()
        return ReviewResult(outcome="needs_revision", reason="Add the missing test.")


class SuccessThenFailureReviewAnalyzer(SystemGateAnalyzer):
    def __init__(self) -> None:
        self.entered = 0
        self.first_entered = asyncio.Event()
        self.both_entered = asyncio.Event()
        self.release_success = asyncio.Event()
        self.release_failure = asyncio.Event()

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        raise AssertionError("backlog analysis should not run")

    async def review_completed_task(self, context: dict) -> ReviewResult:
        self.entered += 1
        call_number = self.entered
        if call_number == 1:
            self.first_entered.set()
        if self.entered == 2:
            self.both_entered.set()
        if call_number == 1:
            await self.release_success.wait()
            return ReviewResult(outcome="needs_revision", reason="Add the missing test.")
        await self.release_failure.wait()
        raise RuntimeError("late review failure")


class BlockingFailureAnalyzer(SystemGateAnalyzer):
    def __init__(self) -> None:
        self.backlog_entered = 0
        self.review_entered = 0
        self.first_backlog_entered = asyncio.Event()
        self.first_review_entered = asyncio.Event()
        self.release = asyncio.Event()

    async def decide_needs_review(self, context: dict) -> BacklogIntakeResult:
        self.backlog_entered += 1
        self.first_backlog_entered.set()
        await self.release.wait()
        raise RuntimeError("backlog crashed")

    async def review_completed_task(self, context: dict) -> ReviewResult:
        self.review_entered += 1
        self.first_review_entered.set()
        await self.release.wait()
        raise RuntimeError("review crashed")


def _agent_analyzer(text: str) -> AgentRunnerSystemGateAnalyzer:
    analyzer = AgentRunnerSystemGateAnalyzer.__new__(AgentRunnerSystemGateAnalyzer)
    analyzer._runner = FakeRunner(text)
    analyzer._project_dir = "/tmp/project"
    return analyzer


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
    _git(repo, "branch", "-M", "main")


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def event_bus():
    return RecordingEventBus()


@pytest.fixture
async def board(db: Database, event_bus: RecordingEventBus) -> TaskBoard:
    task_board = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
        event_bus=event_bus,
    )
    await task_board.register_prefixes(
        {
            "pm": "PM",
            "architect": "AR",
            "coder": "CD",
            "tester": "TS",
            "reviewer": "RV",
        }
    )
    return task_board


async def _create_group(board: TaskBoard) -> dict:
    return await board.create_group(title="Feature", created_by="pm")


async def _create_review_task(board: TaskBoard, output: str = "Ready.") -> dict:
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Build reviewed widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == task["id"]
    return await board.complete_task_with_output(task["id"], output)


def _running_gate_runs(gate: str) -> str:
    return json.dumps(
        [
            {
                "gate": gate,
                "outcome": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )


def _stale_running_gate_runs(gate: str) -> str:
    return json.dumps(
        [
            {
                "gate": gate,
                "outcome": "running",
                "started_at": "2000-01-01T00:00:00+00:00",
            }
        ]
    )


async def test_process_pending_once_processes_one_backlog_task_once(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Build risky widget",
        task_type="implementation",
        assigned_to="coder",
    )
    analyzer = FakeAnalyzer(
        backlog_results=[
            BacklogIntakeResult(
                needs_review=True,
                reason="Touches shared orchestration.",
                signals=["shared_orchestration"],
                confidence="high",
            )
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer, batch_size=10)

    counts = await manager.process_pending_once()
    again = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    assert again == {"backlog": 0, "review": 0, "package_review": 0}
    assert len(analyzer.backlog_contexts) == 1
    assert analyzer.backlog_contexts[0]["task"]["id"] == task["id"]
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["needs_review"] == 1
    assert updated["needs_review_reason"] == "Touches shared orchestration."
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-2]["gate"] == "backlog"
    assert gate_runs[-2]["outcome"] == "running"
    assert gate_runs[-1]["gate"] == "backlog"
    assert gate_runs[-1]["outcome"] == "completed"
    assert gate_runs[-1]["reason"] == "Touches shared orchestration."


async def test_backlog_intake_skips_task_level_review_for_planning_tasks(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Create PRD for simple CLI",
        task_type="goal",
        assigned_to="pm",
    )
    analyzer = FakeAnalyzer()
    manager = SystemGateManager(board=board, analyzer=analyzer, batch_size=10)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    assert analyzer.backlog_contexts == []
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["needs_review"] == 0
    assert "Planning tasks are validated" in updated["needs_review_reason"]
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-1]["needs_review"] is False


async def test_process_pending_once_approves_pending_review_task(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    review_task = await _create_review_task(board, output="Implementation summary.")
    event_bus.events.clear()
    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(outcome="approved", reason="Output satisfies the gate.")
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    assert len(analyzer.review_contexts) == 1
    assert analyzer.review_contexts[0]["task"]["id"] == review_task["id"]
    assert analyzer.review_contexts[0]["output_text"] == "Implementation summary."
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "approved"
    assert event_bus.events[0] == (
        "task.system_gate_started",
        {
            "task_id": review_task["id"],
            "gate": "review",
            "review_status": "running",
        },
    )
    assert event_bus.events[-1][0] == "task.system_gate_finished"
    assert event_bus.events[-1][1]["task_id"] == review_task["id"]
    assert event_bus.events[-1][1]["outcome"] == "approved"
    assert event_bus.events[-1][1]["status"] == "completed"


async def test_planning_review_revisions_stay_with_planning_role(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Create PRD for simple CLI",
        task_type="goal",
        assigned_to="pm",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Manual planning review.",
    )
    claimed = await board.claim_task("pm", "pm-1")
    assert claimed["id"] == task["id"]
    await board.complete_task_with_output(task["id"], "Created PRD and architect task.")
    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(
                outcome="needs_revision",
                reason="PRD needs clearer acceptance criteria.",
                revisions=[
                    RevisionRequest(
                        title="Implement missing CLI",
                        description="Add the actual code.",
                        assigned_to="coder",
                        task_type="implementation",
                        priority="high",
                    )
                ],
            )
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    handled = await manager.process_review_task(task["id"])

    assert handled is True
    original = await board.get_task(task["id"])
    assert original["review_status"] == "waiting_revision"
    revision_ids = json.loads(original["revision_task_ids"])
    assert len(revision_ids) == 1
    revision = await board.get_task(revision_ids[0])
    assert revision["assigned_to"] == "pm"
    assert revision["task_type"] == "revision"
    assert revision["title"] == "Implement missing CLI"


async def test_review_revision_falls_back_to_main_when_parent_branch_missing(
    board: TaskBoard,
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    board.configure_package_integration(repo_dir=str(repo))
    review_task = await _create_review_task(board, output="Implementation summary.")

    created = await board.create_review_revision_tasks(
        review_task["id"],
        [{"title": "Fix missing test"}],
    )

    assert len(created) == 1
    revision = await board.get_task(created[0]["id"])
    assert revision["parent_branch"] == "main"


async def test_process_pending_once_approves_package_review(board: TaskBoard):
    group = await _create_group(board)
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review bundle",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build package feature",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task["id"]
    await board.complete_task_with_output(task["id"], "Implemented package feature.")

    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(outcome="approved", reason="Package satisfies spec.")
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts["package_review"] == 1
    updated_package = await board.get_work_package(package["id"])
    assert updated_package["status"] == "completed"
    assert updated_package["review_status"] == "approved"
    gate = await board._db.execute_fetchone(
        "SELECT * FROM review_gates WHERE entity_type = 'work_package' "
        "AND entity_id = ?",
        (package["id"],),
    )
    assert gate["status"] == "completed"
    assert gate["outcome"] == "approved"
    assert len(analyzer.review_contexts) == 1
    context = analyzer.review_contexts[0]
    assert context["entity_type"] == "work_package"
    assert context["package"]["id"] == package["id"]
    assert context["tasks"][0]["id"] == task["id"]
    groups = await board.get_groups()
    completed_group = next(item for item in groups if item["id"] == group["id"])
    assert completed_group["status"] == "completed"
    assert completed_group["completed_at"] is not None


async def test_package_review_context_includes_branch_diff_and_integration_queue(
    db: Database,
    event_bus: RecordingEventBus,
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "add_two_numbers.py").write_text("print(1 + 2)\n")
    _git(repo, "add", "add_two_numbers.py")
    _git(repo, "commit", "-m", "add script")
    _git(repo, "checkout", "main")

    task_board = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
        event_bus=event_bus,
    )
    await task_board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    queue = MergeQueue(db)
    task_board.configure_package_integration(merge_queue=queue, repo_dir=str(repo))
    group = await task_board.create_group(title="Feature", created_by="pm")
    package = await task_board.create_work_package(
        group_id=group["id"],
        title="Addition CLI",
        description="Deliver a minimal addition script.",
        created_by="architect-1",
    )
    task = await task_board.create_task(
        group_id=group["id"],
        title="Build addition script",
        description="Create add_two_numbers.py and verify it runs.",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
        branch_name="feat/cd-001",
        parent_branch="main",
    )
    await task_board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await task_board.claim_task("coder", "coder-1")
    assert claimed is not None
    await task_board.complete_task_with_output(task["id"], "Added the script.")
    await queue.enqueue_package_integration(
        group_id=group["id"],
        work_package_id=package["id"],
        parent_task_id=task["id"],
        source_branch="feat/cd-001",
        target_branch="main",
    )

    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Looks good.")]
    )
    manager = SystemGateManager(board=task_board, analyzer=analyzer)
    await manager.process_pending_once()

    context = analyzer.review_contexts[0]
    assert context["package"]["description"] == "Deliver a minimal addition script."
    assert context["tasks"][0]["description"] == "Create add_two_numbers.py and verify it runs."
    assert context["integration_queue"][0]["source_type"] == "package_approval"
    assert context["integration_queue"][0]["work_package_id"] == package["id"]
    diff = context["branch_diffs"][0]
    assert diff["task_id"] == task["id"]
    assert diff["source_branch"] == "feat/cd-001"
    assert "add_two_numbers.py" in diff["diff_stat"]
    assert "add script" in diff["commit_log"]


async def test_package_review_analysis_error_marks_failed_and_does_not_raise(
    board: TaskBoard,
):
    group = await _create_group(board)
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review parse failure",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build parse-sensitive package",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task["id"]
    await board.complete_task_with_output(task["id"], "Implemented package feature.")

    analyzer = FakeAnalyzer(review_results=[SystemGateAnalysisError("bad json")])
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 0, "package_review": 0}
    gate = await board._db.execute_fetchone(
        "SELECT * FROM review_gates WHERE entity_type = 'work_package' "
        "AND entity_id = ?",
        (package["id"],),
    )
    assert gate["status"] == "failed"
    assert gate["outcome"] == "failed_review"
    assert gate["reason"] == "bad json"
    gate_runs = json.loads(gate["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "failed_review"
    assert gate_runs[-1]["reason"] == "bad json"


async def test_process_pending_once_retries_stale_running_package_review(
    board: TaskBoard,
):
    group = await _create_group(board)
    package = await board.create_work_package(
        group_id=group["id"],
        title="Stale package review bundle",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build stale package feature",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task["id"]
    await board.complete_task_with_output(task["id"], "Implemented package feature.")
    gate = await board._db.execute_fetchone(
        "SELECT * FROM review_gates WHERE entity_type = 'work_package' "
        "AND entity_id = ?",
        (package["id"],),
    )
    assert gate is not None
    await board._db.execute(
        "UPDATE review_gates SET status = 'running', system_gate_runs = ?, "
        "updated_at = ? WHERE id = ?",
        (
            _stale_running_gate_runs("package_review"),
            "2000-01-01T00:00:00+00:00",
            gate["id"],
        ),
    )

    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(outcome="approved", reason="Package satisfies spec.")
        ]
    )
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=1,
    )

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 0, "package_review": 1}
    updated_package = await board.get_work_package(package["id"])
    assert updated_package["status"] == "completed"
    assert updated_package["review_status"] == "approved"
    updated_gate = await board._db.execute_fetchone(
        "SELECT * FROM review_gates WHERE id = ?",
        (gate["id"],),
    )
    assert updated_gate["status"] == "completed"
    assert updated_gate["outcome"] == "approved"


async def test_process_pending_once_creates_revision_tasks_for_needs_revision(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(
                outcome="needs_revision",
                reason="Missing tests.",
                revisions=[
                    RevisionRequest(
                        title="Add regression tests",
                        description="Cover the review finding.",
                        assigned_to="tester",
                        task_type="qa_verification",
                        priority="high",
                    )
                ],
            )
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "review"
    assert updated["review_status"] == "waiting_revision"
    revision_ids = json.loads(updated["revision_task_ids"])
    assert len(revision_ids) == 1
    revision = await board.get_task(revision_ids[0])
    assert revision["status"] == "backlog"
    assert revision["title"] == "Add regression tests"
    assert revision["description"] == "Cover the review finding."
    assert revision["assigned_to"] == "tester"
    assert updated["review_round"] == 1


async def test_process_pending_once_defaults_revision_task_when_missing(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="needs_revision", reason="Fix the gap.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    assert len(revision_ids) == 1
    revision = await board.get_task(revision_ids[0])
    assert revision["title"] == f"Revision 1 for {review_task['id']}"
    assert revision["description"] == "Fix the gap."


async def test_process_pending_once_rejects_when_review_round_limit_reached(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_round = 3, max_review_rounds = 3 WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(
                outcome="needs_revision",
                reason="Still missing required tests.",
                revisions=[RevisionRequest(title="Add tests")],
            )
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "rejected"
    assert updated["review_status"] == "rejected"
    assert json.loads(updated["revision_task_ids"]) == []
    assert "failed to pass review after 3 review rounds" in updated["rejection_reason"]


async def test_process_pending_once_skips_active_running_backlog_task(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Do not retry active backlog",
        task_type="implementation",
        assigned_to="coder",
    )
    await board._db.execute(
        "UPDATE tasks SET backlog_intake_status = 'running', system_gate_runs = ? "
        "WHERE id = ?",
        (_running_gate_runs("backlog"), task["id"]),
    )
    analyzer = FakeAnalyzer(
        backlog_results=[
            BacklogIntakeResult(needs_review=False, reason="Must not be consumed.")
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 0, "package_review": 0}
    assert analyzer.backlog_contexts == []
    updated = await board.get_task(task["id"])
    assert updated["status"] == "backlog"
    assert updated["backlog_intake_status"] == "running"


async def test_process_pending_once_retries_running_backlog_without_audit(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Retry unaudited running backlog",
        task_type="implementation",
        assigned_to="coder",
    )
    await board._db.execute(
        "UPDATE tasks SET backlog_intake_status = 'running' WHERE id = ?",
        (task["id"],),
    )
    analyzer = FakeAnalyzer(
        backlog_results=[BacklogIntakeResult(needs_review=False, reason="Retry.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["backlog_intake_status"] == "completed"


async def test_process_pending_once_retries_running_backlog_with_naive_timestamp(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Retry naive timestamp backlog",
        task_type="implementation",
        assigned_to="coder",
    )
    runs = json.dumps(
        [
            {
                "gate": "backlog",
                "outcome": "running",
                "started_at": "2026-05-05T12:00:00",
            }
        ]
    )
    await board._db.execute(
        "UPDATE tasks SET backlog_intake_status = 'running', system_gate_runs = ? "
        "WHERE id = ?",
        (runs, task["id"]),
    )
    analyzer = FakeAnalyzer(
        backlog_results=[BacklogIntakeResult(needs_review=False, reason="Retry.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"


async def test_active_running_backlog_does_not_starve_pending_backlog(
    board: TaskBoard,
):
    group = await _create_group(board)
    active = await board.create_task(
        group_id=group["id"],
        title="Active running backlog",
        task_type="implementation",
        assigned_to="coder",
    )
    pending = await board.create_task(
        group_id=group["id"],
        title="Pending backlog",
        task_type="implementation",
        assigned_to="coder",
    )
    await board._db.execute(
        "UPDATE tasks SET backlog_intake_status = 'running', system_gate_runs = ? "
        "WHERE id = ?",
        (_running_gate_runs("backlog"), active["id"]),
    )
    analyzer = FakeAnalyzer(
        backlog_results=[BacklogIntakeResult(needs_review=False, reason="Pending.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer, batch_size=1)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    active_updated = await board.get_task(active["id"])
    pending_updated = await board.get_task(pending["id"])
    assert active_updated["status"] == "backlog"
    assert active_updated["backlog_intake_status"] == "running"
    assert pending_updated["status"] == "pending"


async def test_many_active_running_backlog_rows_do_not_starve_pending_backlog(
    board: TaskBoard,
):
    group = await _create_group(board)
    active_tasks = [
        await board.create_task(
            group_id=group["id"],
            title=f"Active running backlog {index}",
            task_type="implementation",
            assigned_to="coder",
        )
        for index in range(30)
    ]
    pending = await board.create_task(
        group_id=group["id"],
        title="Pending backlog after active rows",
        task_type="implementation",
        assigned_to="coder",
    )
    for active in active_tasks:
        await board._db.execute(
            "UPDATE tasks SET backlog_intake_status = 'running', system_gate_runs = ? "
            "WHERE id = ?",
            (_running_gate_runs("backlog"), active["id"]),
        )
    analyzer = FakeAnalyzer(
        backlog_results=[BacklogIntakeResult(needs_review=False, reason="Pending.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer, batch_size=1)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    pending_updated = await board.get_task(pending["id"])
    assert pending_updated["status"] == "pending"
    assert len(analyzer.backlog_contexts) == 1
    assert analyzer.backlog_contexts[0]["task"]["id"] == pending["id"]


async def test_process_pending_once_retries_stale_running_backlog_task(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Retry stranded backlog",
        task_type="implementation",
        assigned_to="coder",
    )
    await board._db.execute(
        "UPDATE tasks SET backlog_intake_status = 'running' WHERE id = ?",
        (task["id"],),
    )
    analyzer = FakeAnalyzer(
        backlog_results=[
            BacklogIntakeResult(
                needs_review=False,
                reason="Small isolated task.",
            )
        ]
    )
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 1, "review": 0, "package_review": 0}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["backlog_intake_status"] == "completed"


async def test_process_pending_once_skips_active_running_review_task(board: TaskBoard):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running', system_gate_runs = ? "
        "WHERE id = ?",
        (_running_gate_runs("review"), review_task["id"]),
    )
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Must not be consumed.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 0, "package_review": 0}
    assert analyzer.review_contexts == []
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "review"
    assert updated["review_status"] == "running"


async def test_process_pending_once_retries_running_review_without_audit(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Retry.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"


async def test_process_pending_once_retries_running_review_with_naive_timestamp(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    runs = json.dumps(
        [
            {
                "gate": "review",
                "outcome": "running",
                "started_at": "2026-05-05T12:00:00",
            }
        ]
    )
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running', system_gate_runs = ? "
        "WHERE id = ?",
        (runs, review_task["id"]),
    )
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Retry.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "completed"


async def test_active_running_review_does_not_starve_pending_review(
    board: TaskBoard,
):
    active = await _create_review_task(board)
    pending = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running', system_gate_runs = ? "
        "WHERE id = ?",
        (_running_gate_runs("review"), active["id"]),
    )
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Pending.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer, batch_size=1)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    active_updated = await board.get_task(active["id"])
    pending_updated = await board.get_task(pending["id"])
    assert active_updated["status"] == "review"
    assert active_updated["review_status"] == "running"
    assert pending_updated["status"] == "completed"
    assert pending_updated["review_status"] == "approved"


async def test_many_active_running_review_rows_do_not_starve_pending_review(
    board: TaskBoard,
):
    active_tasks = [await _create_review_task(board) for _ in range(30)]
    pending = await _create_review_task(board)
    for active in active_tasks:
        await board._db.execute(
            "UPDATE tasks SET review_status = 'running', system_gate_runs = ? "
            "WHERE id = ?",
            (_running_gate_runs("review"), active["id"]),
        )
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Pending.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer, batch_size=1)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    pending_updated = await board.get_task(pending["id"])
    assert pending_updated["status"] == "completed"
    assert pending_updated["review_status"] == "approved"
    assert len(analyzer.review_contexts) == 1
    assert analyzer.review_contexts[0]["task"]["id"] == pending["id"]


async def test_process_pending_once_retries_stale_running_review_task(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Still good.")]
    )
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"


async def test_overlapping_review_tasks_create_one_revision(board: TaskBoard):
    review_task = await _create_review_task(board)
    analyzer = BlockingReviewAnalyzer()
    manager = SystemGateManager(board=board, analyzer=analyzer)

    first = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.first_entered.wait()
    second = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await asyncio.sleep(0)
    analyzer.release.set()

    results = await asyncio.gather(first, second)

    assert results.count(True) == 1
    assert results.count(False) == 1
    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    gate_runs = json.loads(updated["system_gate_runs"])
    needs_revision_runs = [
        run for run in gate_runs if run.get("outcome") == "needs_revision"
    ]
    assert len(revision_ids) == 1
    assert len(needs_revision_runs) == 1
    assert analyzer.entered == 1


async def test_overlapping_stale_review_retries_create_one_revision(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = BlockingReviewAnalyzer()
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.first_entered.wait()
    second = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await asyncio.sleep(0)
    analyzer.release.set()

    results = await asyncio.gather(first, second)

    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    gate_runs = json.loads(updated["system_gate_runs"])
    needs_revision_runs = [
        run for run in gate_runs if run.get("outcome") == "needs_revision"
    ]
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert analyzer.entered == 1
    assert len(revision_ids) == 1
    assert len(needs_revision_runs) == 1


async def test_late_failing_stale_review_retry_does_not_override_revision(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = BlockingReviewAnalyzer()
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.first_entered.wait()
    analyzer.release.set()

    for _ in range(50):
        updated = await board.get_task(review_task["id"])
        if updated["review_status"] == "waiting_revision":
            break
        await asyncio.sleep(0.01)
    second = await board.mark_review_failed(review_task["id"], "late review failure")

    results = await asyncio.gather(first)

    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    gate_runs = json.loads(updated["system_gate_runs"])
    needs_revision_runs = [
        run for run in gate_runs if run.get("outcome") == "needs_revision"
    ]
    failed_review_runs = [
        run for run in gate_runs if run.get("outcome") == "failed_review"
    ]
    assert results == [True]
    assert second["review_status"] == "waiting_revision"
    assert updated["review_status"] == "waiting_revision"
    assert len(revision_ids) == 1
    assert len(needs_revision_runs) == 1
    assert failed_review_runs == []


async def test_overlapping_stale_review_retries_enter_analyzer_once(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = BlockingReviewAnalyzer()
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.first_entered.wait()
    second = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await asyncio.sleep(0)
    analyzer.release.set()

    results = await asyncio.gather(first, second)

    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert analyzer.entered == 1
    assert len(revision_ids) == 1


async def test_two_managers_share_review_gate_lock(board: TaskBoard):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = BlockingReviewAnalyzer()
    first_manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )
    second_manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(first_manager.process_review_task(review_task["id"]))
    await analyzer.first_entered.wait()
    second = asyncio.create_task(second_manager.process_review_task(review_task["id"]))
    await asyncio.sleep(0)
    analyzer.release.set()

    results = await asyncio.gather(first, second)

    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    gate_runs = json.loads(updated["system_gate_runs"])
    needs_revision_runs = [
        run for run in gate_runs if run.get("outcome") == "needs_revision"
    ]
    assert results.count(True) == 1
    assert results.count(False) == 1
    assert analyzer.entered == 1
    assert len(revision_ids) == 1
    assert len(needs_revision_runs) == 1


async def test_queued_review_retry_after_failure_does_not_reenter_analyzer(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board._db.execute(
        "UPDATE tasks SET review_status = 'running' WHERE id = ?",
        (review_task["id"],),
    )
    analyzer = BlockingFailureAnalyzer()
    first_manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )
    second_manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(first_manager.process_review_task(review_task["id"]))
    await analyzer.first_review_entered.wait()
    second = asyncio.create_task(second_manager.process_review_task(review_task["id"]))
    await asyncio.sleep(0)
    analyzer.release.set()

    results = await asyncio.gather(first, second)

    updated = await board.get_task(review_task["id"])
    assert results == [False, False]
    assert analyzer.review_entered == 1
    assert updated["review_status"] == "failed"

    retry_analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="approved", reason="Retry later.")]
    )
    retry_manager = SystemGateManager(
        board=board,
        analyzer=retry_analyzer,
        retry_cooldown_seconds=0,
    )

    assert await retry_manager.process_review_task(review_task["id"]) is True
    assert len(retry_analyzer.review_contexts) == 1


async def test_queued_backlog_retry_after_failure_does_not_reenter_analyzer(
    board: TaskBoard,
):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Fail backlog once",
        task_type="implementation",
        assigned_to="coder",
    )
    await board._db.execute(
        "UPDATE tasks SET backlog_intake_status = 'running' WHERE id = ?",
        (task["id"],),
    )
    analyzer = BlockingFailureAnalyzer()
    first_manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )
    second_manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(first_manager.process_backlog_task(task["id"]))
    await analyzer.first_backlog_entered.wait()
    second = asyncio.create_task(second_manager.process_backlog_task(task["id"]))
    await asyncio.sleep(0)
    analyzer.release.set()

    results = await asyncio.gather(first, second)

    updated = await board.get_task(task["id"])
    assert results == [False, False]
    assert analyzer.backlog_entered == 1
    assert updated["backlog_intake_status"] == "failed"

    retry_analyzer = FakeAnalyzer(
        backlog_results=[BacklogIntakeResult(needs_review=False, reason="Retry later.")]
    )
    retry_manager = SystemGateManager(
        board=board,
        analyzer=retry_analyzer,
        retry_cooldown_seconds=0,
    )

    assert await retry_manager.process_backlog_task(task["id"]) is True
    assert len(retry_analyzer.backlog_contexts) == 1


async def test_create_review_revision_tasks_rolls_back_empty_transition(
    board: TaskBoard,
    monkeypatch: pytest.MonkeyPatch,
):
    review_task = await _create_review_task(board)

    async def fail_create_task(*args, **kwargs):
        raise RuntimeError("create failed")

    monkeypatch.setattr(board, "create_task", fail_create_task)

    with pytest.raises(RuntimeError, match="create failed"):
        await board.create_review_revision_tasks(
            review_task["id"],
            [{"description": "Fix this first."}],
        )

    updated = await board.get_task(review_task["id"])
    deps = await board._db.execute_fetchall(
        "SELECT * FROM task_dependencies WHERE task_id = ?",
        (review_task["id"],),
    )
    assert updated["review_status"] == "pending"
    assert json.loads(updated["revision_task_ids"]) == []
    assert deps == []


async def test_backlog_analyzer_exception_marks_intake_failed(board: TaskBoard):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Fail backlog analysis",
        task_type="implementation",
        assigned_to="coder",
    )
    analyzer = FakeAnalyzer(backlog_results=[RuntimeError("model unavailable")])
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 0, "package_review": 0}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "backlog"
    assert updated["backlog_intake_status"] == "failed"
    assert "model unavailable" in updated["needs_review_reason"]


async def test_backlog_cancelled_error_marks_failed_and_reraises(board: TaskBoard):
    group = await _create_group(board)
    task = await board.create_task(
        group_id=group["id"],
        title="Cancelled backlog analysis",
        task_type="implementation",
        assigned_to="coder",
    )
    analyzer = FakeAnalyzer(backlog_results=[asyncio.CancelledError("stopping")])
    manager = SystemGateManager(board=board, analyzer=analyzer)

    with pytest.raises(asyncio.CancelledError):
        await manager.process_backlog_task(task["id"])

    updated = await board.get_task(task["id"])
    assert updated["backlog_intake_status"] == "failed"


async def test_review_failed_review_outcome_marks_failed_and_appends_audit(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(outcome="failed_review", reason="Could not inspect output.")
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "review"
    assert updated["review_status"] == "failed"
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "failed_review"
    assert gate_runs[-1]["reason"] == "Could not inspect output."


async def test_review_analyzer_exception_marks_failed_and_appends_audit(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    analyzer = FakeAnalyzer(review_results=[RuntimeError("review crashed")])
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 0, "package_review": 0}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "review"
    assert updated["review_status"] == "failed"
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "failed_review"
    assert gate_runs[-1]["reason"] == "review crashed"


async def test_review_cancelled_error_marks_failed_and_reraises(board: TaskBoard):
    review_task = await _create_review_task(board)
    analyzer = FakeAnalyzer(review_results=[asyncio.CancelledError("stopping")])
    manager = SystemGateManager(board=board, analyzer=analyzer)

    with pytest.raises(asyncio.CancelledError):
        await manager.process_review_task(review_task["id"])

    updated = await board.get_task(review_task["id"])
    assert updated["review_status"] == "failed"
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "failed_review"


async def test_reject_review_gate_skips_waiting_revision_without_cascade(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    await board.create_review_revision_tasks(
        review_task["id"],
        [{"description": "Fix this first."}],
    )

    unchanged = await board.reject_review_gate(
        review_task["id"],
        reason="Late reject should not override revision wait.",
    )

    assert unchanged["status"] == "review"
    assert unchanged["review_status"] == "waiting_revision"
    fresh = await board.get_task(review_task["id"])
    assert fresh["status"] == "review"
    assert fresh["review_status"] == "waiting_revision"
    assert fresh["rejection_reason"] is None


async def test_reject_review_gate_skips_unresolved_dependency_without_cascade(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    revision = await board.create_review_revision_tasks(
        review_task["id"],
        [{"description": "Fix this first."}],
    )
    await board._db.execute(
        "UPDATE tasks SET review_status = 'pending' WHERE id = ?",
        (review_task["id"],),
    )

    unchanged = await board.reject_review_gate(
        review_task["id"],
        reason="Dependency still unresolved.",
    )

    assert unchanged["status"] == "review"
    assert unchanged["review_status"] == "pending"
    fresh = await board.get_task(review_task["id"])
    assert fresh["status"] == "review"
    assert fresh["review_status"] == "pending"
    revision_task = await board.get_task(revision[0]["id"])
    assert revision_task["status"] == "backlog"


def test_extract_json_object_accepts_embedded_json() -> None:
    assert _extract_json_object('prefix ```json\n{"ok": true}\n``` suffix') == {
        "ok": True
    }


def test_extract_json_object_returns_last_parseable_object() -> None:
    text = (
        'Example schema: {"needs_review": true, "reason": "example"}\n'
        'Final answer: {"needs_review": false, "reason": "small task"}'
    )

    assert _extract_json_object(text) == {
        "needs_review": False,
        "reason": "small task",
    }


def test_extract_json_object_prefers_fenced_json_object() -> None:
    text = (
        'Loose example: {"needs_review": true, "reason": "example"}\n'
        "```json\n"
        '{"needs_review": false, "reason": "final fenced"}\n'
        "```"
    )

    assert _extract_json_object(text) == {
        "needs_review": False,
        "reason": "final fenced",
    }


def test_extract_json_object_keeps_nested_review_response_toplevel() -> None:
    text = (
        '{"outcome":"needs_revision","reason":"x",'
        '"revisions":[{"title":"t","description":"d"}]}'
    )

    assert _extract_json_object(text) == {
        "outcome": "needs_revision",
        "reason": "x",
        "revisions": [{"title": "t", "description": "d"}],
    }


def test_extract_json_object_rejects_missing_json() -> None:
    with pytest.raises(SystemGateAnalysisError, match="did not contain"):
        _extract_json_object("no structured output")


def test_extract_json_object_rejects_invalid_json() -> None:
    with pytest.raises(SystemGateAnalysisError, match="invalid"):
        _extract_json_object('{"ok": true trailing}')


def test_extract_json_object_rejects_non_object_json() -> None:
    with pytest.raises(SystemGateAnalysisError, match="must be an object"):
        _extract_json_object("[1, 2, 3]")


def test_system_agent_import_smoke() -> None:
    import taskbrew.system_agent as system_agent

    assert system_agent.SYSTEM_AGENT_PROMPT
    assert callable(system_agent.build_system_agent_config)


def test_build_system_agent_config_smoke(tmp_path) -> None:
    from taskbrew.system_agent import build_system_agent_config

    team_config = SimpleNamespace(
        cli_provider="codex",
        system_agent=SimpleNamespace(
            provider="codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
        ),
        db_path="data/tasks.db",
        mcp_servers=None,
    )

    config = build_system_agent_config(team_config, project_dir=tmp_path)

    assert config.name == "system"
    assert config.model == "gpt-5.4-mini"
    assert config.reasoning_effort == "low"


async def test_agent_analyzer_rejects_wrong_needs_review_type() -> None:
    analyzer = _agent_analyzer('{"needs_review": "yes", "reason": "Risky."}')

    with pytest.raises(SystemGateAnalysisError, match="needs_review"):
        await analyzer.decide_needs_review({"task": {"id": "CD-001"}})


async def test_agent_analyzer_rejects_invalid_review_outcome() -> None:
    analyzer = _agent_analyzer('{"outcome": "maybe", "reason": "Unsure."}')

    with pytest.raises(SystemGateAnalysisError, match="outcome"):
        await analyzer.review_completed_task({"task": {"id": "CD-001"}})


async def test_system_gate_manager_exposes_stop_method(board: TaskBoard):
    manager = SystemGateManager(board=board, analyzer=FakeAnalyzer())

    manager.stop()

    assert manager._stop_requested is True


async def test_start_system_gate_manager_skips_existing_live_task() -> None:
    from taskbrew.main import _start_system_gate_manager

    manager = FakeSystemGateManager()
    orch = SimpleNamespace(
        system_gate_manager=manager,
        _system_gate_task=None,
        agent_tasks=[],
    )

    _start_system_gate_manager(orch)
    first_task = orch._system_gate_task
    await asyncio.sleep(0)
    _start_system_gate_manager(orch)

    assert orch._system_gate_task is first_task
    assert orch.agent_tasks == [first_task]
    assert manager.run_count == 1

    manager.release.set()
    await first_task


async def test_start_system_gate_manager_restarts_done_task() -> None:
    from taskbrew.main import _start_system_gate_manager

    manager = FakeSystemGateManager()
    orch = SimpleNamespace(
        system_gate_manager=manager,
        _system_gate_task=None,
        agent_tasks=[],
    )

    _start_system_gate_manager(orch)
    first_task = orch._system_gate_task
    manager.release.set()
    await first_task
    manager.release = asyncio.Event()

    _start_system_gate_manager(orch)
    second_task = orch._system_gate_task
    await asyncio.sleep(0)

    assert second_task is not first_task
    assert orch.agent_tasks == [first_task, second_task]
    assert manager.run_count == 2

    manager.release.set()
    await second_task
