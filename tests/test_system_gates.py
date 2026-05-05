"""Tests for the async system gate manager."""

from __future__ import annotations

import json

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.system_gates import (
    BacklogIntakeResult,
    RevisionRequest,
    ReviewResult,
    SystemGateAnalyzer,
    SystemGateManager,
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
        return self.backlog_results.pop(0)

    async def review_completed_task(self, context: dict) -> ReviewResult:
        self.review_contexts.append(context)
        return self.review_results.pop(0)


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
