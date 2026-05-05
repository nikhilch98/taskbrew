"""Tests for the async system gate manager."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
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


def _agent_analyzer(text: str) -> AgentRunnerSystemGateAnalyzer:
    analyzer = AgentRunnerSystemGateAnalyzer.__new__(AgentRunnerSystemGateAnalyzer)
    analyzer._runner = FakeRunner(text)
    analyzer._project_dir = "/tmp/project"
    return analyzer


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

    assert counts == {"backlog": 1, "review": 0}
    assert again == {"backlog": 0, "review": 0}
    assert len(analyzer.backlog_contexts) == 1
    assert analyzer.backlog_contexts[0]["task"]["id"] == task["id"]
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["needs_review"] == 1
    assert updated["needs_review_reason"] == "Touches shared orchestration."


async def test_process_pending_once_approves_pending_review_task(board: TaskBoard):
    review_task = await _create_review_task(board, output="Implementation summary.")
    analyzer = FakeAnalyzer(
        review_results=[
            ReviewResult(outcome="approved", reason="Output satisfies the gate.")
        ]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1}
    assert len(analyzer.review_contexts) == 1
    assert analyzer.review_contexts[0]["task"]["id"] == review_task["id"]
    assert analyzer.review_contexts[0]["output_text"] == "Implementation summary."
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"
    gate_runs = json.loads(updated["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "approved"


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

    assert counts == {"backlog": 0, "review": 1}
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


async def test_process_pending_once_defaults_revision_task_when_missing(
    board: TaskBoard,
):
    review_task = await _create_review_task(board)
    analyzer = FakeAnalyzer(
        review_results=[ReviewResult(outcome="needs_revision", reason="Fix the gap.")]
    )
    manager = SystemGateManager(board=board, analyzer=analyzer)

    counts = await manager.process_pending_once()

    assert counts == {"backlog": 0, "review": 1}
    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    assert len(revision_ids) == 1
    revision = await board.get_task(revision_ids[0])
    assert revision["title"] == f"Revision 1 for {review_task['id']}"
    assert revision["description"] == "Fix the gap."


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

    assert counts == {"backlog": 0, "review": 0}
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

    assert counts == {"backlog": 1, "review": 0}
    updated = await board.get_task(task["id"])
    assert updated["status"] == "pending"
    assert updated["backlog_intake_status"] == "completed"


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

    assert counts == {"backlog": 1, "review": 0}
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

    assert counts == {"backlog": 1, "review": 0}
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

    assert counts == {"backlog": 1, "review": 0}
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

    assert counts == {"backlog": 0, "review": 0}
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

    assert counts == {"backlog": 0, "review": 1}
    updated = await board.get_task(review_task["id"])
    assert updated["status"] == "completed"
    assert updated["review_status"] == "approved"


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

    assert counts == {"backlog": 0, "review": 1}
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

    assert counts == {"backlog": 0, "review": 1}
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

    assert counts == {"backlog": 0, "review": 1}
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
    analyzer = TwoCallerReviewAnalyzer()
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(manager.process_review_task(review_task["id"]))
    second = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.both_entered.wait()
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
    assert analyzer.entered == 2
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
    analyzer = SuccessThenFailureReviewAnalyzer()
    manager = SystemGateManager(
        board=board,
        analyzer=analyzer,
        running_timeout_seconds=0,
    )

    first = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.first_entered.wait()
    second = asyncio.create_task(manager.process_review_task(review_task["id"]))
    await analyzer.both_entered.wait()
    analyzer.release_success.set()

    for _ in range(50):
        updated = await board.get_task(review_task["id"])
        if updated["review_status"] == "waiting_revision":
            break
        await asyncio.sleep(0.01)
    analyzer.release_failure.set()

    results = await asyncio.gather(first, second)

    updated = await board.get_task(review_task["id"])
    revision_ids = json.loads(updated["revision_task_ids"])
    gate_runs = json.loads(updated["system_gate_runs"])
    needs_revision_runs = [
        run for run in gate_runs if run.get("outcome") == "needs_revision"
    ]
    failed_review_runs = [
        run for run in gate_runs if run.get("outcome") == "failed_review"
    ]
    assert results == [True, False]
    assert updated["review_status"] == "waiting_revision"
    assert len(revision_ids) == 1
    assert len(needs_revision_runs) == 1
    assert failed_review_runs == []


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

    assert counts == {"backlog": 0, "review": 0}
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

    assert counts == {"backlog": 0, "review": 1}
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

    assert counts == {"backlog": 0, "review": 0}
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


async def test_agent_analyzer_rejects_wrong_needs_review_type() -> None:
    analyzer = _agent_analyzer('{"needs_review": "yes", "reason": "Risky."}')

    with pytest.raises(SystemGateAnalysisError, match="needs_review"):
        await analyzer.decide_needs_review({"task": {"id": "CD-001"}})


async def test_agent_analyzer_rejects_invalid_review_outcome() -> None:
    analyzer = _agent_analyzer('{"outcome": "maybe", "reason": "Unsure."}')

    with pytest.raises(SystemGateAnalysisError, match="outcome"):
        await analyzer.review_completed_task({"task": {"id": "CD-001"}})
