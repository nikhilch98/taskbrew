"""Tests for Backlog and Review system-gate task workflow."""

from __future__ import annotations

import json

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard


class RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


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


async def test_task_schema_has_system_gate_columns(db: Database):
    columns = {
        row["name"]: row
        for row in await db.execute_fetchall("PRAGMA table_info(tasks)")
    }

    expected = {
        "intended_status",
        "needs_review",
        "needs_review_reason",
        "needs_review_decision",
        "backlog_intake_status",
        "backlog_intake_processed_at",
        "review_status",
        "review_round",
        "max_review_rounds",
        "review_parent_task_id",
        "revision_task_ids",
        "system_gate_runs",
    }
    assert expected.issubset(columns.keys())


async def test_create_task_starts_in_backlog_with_pending_intent(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="pm",
    )

    assert task["status"] == "backlog"
    assert task["intended_status"] == "pending"
    assert task["backlog_intake_status"] == "pending"
    assert task["needs_review"] is None
    assert event_bus.events == []

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is None


async def test_create_blocked_task_starts_in_backlog_with_blocked_intent(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )

    blocked = await board.create_task(
        group_id=group["id"],
        title="Blocked work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )

    assert blocked["status"] == "backlog"
    assert blocked["intended_status"] == "blocked"
    assert blocked["revision_task_ids"] == []
    assert blocked["system_gate_runs"] == []
    deps = await board._db.execute_fetchall(
        "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
        (blocked["id"],),
    )
    assert deps == [{"blocked_by": blocker["id"], "resolved": 0}]


async def test_apply_backlog_intake_decision_moves_pending_task_and_emits_available(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )

    updated = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Touches shared orchestration logic.",
        signals=["shared_orchestration_logic"],
        confidence="medium",
    )

    assert updated["status"] == "pending"
    assert updated["needs_review"] == 1
    assert updated["needs_review_reason"] == "Touches shared orchestration logic."
    decision = json.loads(updated["needs_review_decision"])
    assert decision["decision"] is True
    assert decision["signals"] == ["shared_orchestration_logic"]
    assert decision["confidence"] == "medium"
    assert decision["decided_by"] == "system_agent"
    assert decision["decided_at"]
    assert updated["backlog_intake_status"] == "completed"
    assert event_bus.events[-1] == (
        "task.available",
        {"task_id": task["id"], "role": "coder", "group_id": group["id"]},
    )


async def test_apply_backlog_intake_decision_runs_once(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )

    first = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Small docs-only change.",
    )
    second = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Second decision must not overwrite the first.",
    )

    assert first["status"] == "pending"
    assert second["status"] == "pending"
    assert second["needs_review"] == 0
    assert second["needs_review_reason"] == "Small docs-only change."


async def test_complete_task_with_output_routes_review_required_task_to_review(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build risky widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=True,
        reason="Touches shared orchestration logic.",
    )

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == task["id"]
    completed = await board.complete_task_with_output(
        task["id"],
        "Implemented the shared orchestration change.",
    )

    assert completed["status"] == "review"
    assert completed["review_status"] == "pending"
    assert completed["output_text"] == "Implemented the shared orchestration change."


async def test_complete_task_routes_non_review_task_directly_to_completed(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build simple widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Small isolated task.",
    )

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == task["id"]
    completed = await board.complete_task(task["id"])

    assert completed["status"] == "completed"
    assert completed["review_status"] is None


async def test_review_entry_does_not_release_dependents_until_approved(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=True,
        reason="Touches shared state transitions.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for reviewed implementation.",
    )
    event_bus.events.clear()

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    completed = await board.complete_task_with_output(blocker["id"], "Ready.")

    assert completed["status"] == "review"
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "blocked"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 0}]
    assert event_bus.events == []

    await board.approve_review_gate(blocker["id"], reason="Implementation passes review.")

    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "pending"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 1}]
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": dependent["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_review_rejection_cascades_to_blocked_dependent(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=True,
        reason="Touches shared state transitions.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for reviewed implementation.",
    )
    event_bus.events.clear()

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    completed = await board.complete_task(blocker["id"])
    assert completed["status"] == "review"

    rejected = await board.reject_review_gate(
        blocker["id"],
        reason="Review found the foundation unsafe.",
    )

    assert rejected["status"] == "rejected"
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "failed"
    assert stored_dependent["status"] != "pending"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 0}]
    assert event_bus.events == []


async def test_approve_review_gate_completes_review_task(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
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
    await board.complete_task_with_output(task["id"], "Ready for review.")

    approved = await board.approve_review_gate(
        task["id"],
        reason="Output satisfies the gate.",
    )

    assert approved["status"] == "completed"
    assert approved["review_status"] == "approved"
    assert approved["rejection_reason"] is None
    gate_runs = json.loads(approved["system_gate_runs"])
    assert gate_runs[-1]["gate"] == "review"
    assert gate_runs[-1]["outcome"] == "approved"
    assert gate_runs[-1]["reason"] == "Output satisfies the gate."
    assert gate_runs[-1]["finished_at"]


async def test_reject_review_gate_rejects_review_task_and_closes_group(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
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
    await board.complete_task_with_output(task["id"], "Ready for review.")

    rejected = await board.reject_review_gate(
        task["id"],
        reason="Missing required verification notes.",
    )

    assert rejected["status"] == "rejected"
    assert rejected["review_status"] == "rejected"
    assert rejected["rejection_reason"] == "Missing required verification notes."
    gate_runs = json.loads(rejected["system_gate_runs"])
    assert gate_runs[-1]["gate"] == "review"
    assert gate_runs[-1]["outcome"] == "rejected"
    assert gate_runs[-1]["reason"] == "Missing required verification notes."
    assert gate_runs[-1]["finished_at"]

    groups = await board.get_groups()
    assert groups == [
        {
            **group,
            "status": "completed",
            "completed_at": groups[0]["completed_at"],
        }
    ]
    assert groups[0]["completed_at"]


async def test_apply_backlog_intake_decision_does_not_emit_when_update_loses_race(
    board: TaskBoard,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
    )
    original_execute_returning = board._db.execute_returning

    async def execute_returning_lost_race(sql: str, params: tuple = ()) -> list[dict]:
        rows = await original_execute_returning(sql, params)
        if "WHERE id = ? AND status = 'backlog'" in sql:
            return []
        return rows

    monkeypatch.setattr(board._db, "execute_returning", execute_returning_lost_race)

    updated = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Small docs-only change.",
    )

    assert updated["status"] == "pending"
    assert event_bus.events == []


async def test_failing_blocker_cascades_to_backlog_dependent(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Blocked work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Implementation task.",
    )

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.fail_task(blocker["id"])

    failed_dependent = await board.get_task(dependent["id"])
    assert failed_dependent["status"] == "failed"


async def test_apply_backlog_intake_decision_corrects_stale_blocked_target(
    board: TaskBoard,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Blocked work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    await board._db.execute(
        "UPDATE task_dependencies SET resolved = 1 WHERE task_id = ?",
        (dependent["id"],),
    )

    async def stale_target_status(task_id: str, intended_status: str) -> str:
        return "blocked"

    monkeypatch.setattr(board, "_target_status_after_intake", stale_target_status)

    updated = await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Dependency resolved during intake.",
    )

    assert updated["status"] == "pending"
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": dependent["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_blocked_intent_without_dependency_rows_stays_blocked(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    task = await board.create_task(
        group_id=group["id"],
        title="Blocked before dependency rows exist",
        task_type="qa_verification",
        assigned_to="tester",
    )
    await board._db.execute(
        "UPDATE tasks SET intended_status = 'blocked' WHERE id = ?",
        (task["id"],),
    )

    updated = await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Intake raced with dependency creation.",
    )

    assert updated["status"] == "blocked"
    assert event_bus.events == []


async def test_apply_backlog_intake_decision_refreshes_after_promotion_race(
    board: TaskBoard,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Blocked work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    await board._db.execute(
        "UPDATE task_dependencies SET resolved = 1 WHERE task_id = ?",
        (dependent["id"],),
    )

    async def stale_target_status(task_id: str, intended_status: str) -> str:
        return "blocked"

    original_execute_returning = board._db.execute_returning

    async def execute_returning_promotion_race(
        sql: str, params: tuple = ()
    ) -> list[dict]:
        rows = await original_execute_returning(sql, params)
        if "UPDATE tasks SET status = 'pending'" in sql:
            return []
        return rows

    monkeypatch.setattr(board, "_target_status_after_intake", stale_target_status)
    monkeypatch.setattr(board._db, "execute_returning", execute_returning_promotion_race)

    updated = await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Dependency resolver won promotion race.",
    )

    assert updated["status"] == "pending"
    assert event_bus.events == []


async def test_dependent_created_after_blocker_completed_intakes_to_pending(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Already completed work",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready to run.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.complete_task(blocker["id"])
    event_bus.events.clear()

    dependent = await board.create_task(
        group_id=group["id"],
        title="Depends on completed work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    updated = await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Dependency already completed.",
    )

    deps = await board._db.execute_fetchall(
        "SELECT blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert updated["status"] == "pending"
    assert deps == [{"blocked_by": blocker["id"], "resolved": 1}]
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": dependent["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_resolve_dependencies_does_not_emit_when_update_loses_race(
    board: TaskBoard,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Blocked work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waiting on blocker.",
    )
    event_bus.events.clear()
    original_execute_fetchall = board._db.execute_fetchall

    async def execute_fetchall_promoted_elsewhere(
        sql: str, params: tuple = ()
    ) -> list[dict]:
        rows = await original_execute_fetchall(sql, params)
        if "SELECT t.id, t.assigned_to, t.group_id FROM tasks t" in sql:
            await board._db.execute(
                "UPDATE tasks SET status = 'pending' WHERE id = ?",
                (dependent["id"],),
            )
        return rows

    monkeypatch.setattr(board._db, "execute_fetchall", execute_fetchall_promoted_elsewhere)

    await board._resolve_dependencies(blocker["id"])

    assert event_bus.events == []


async def test_create_task_reconciles_intake_before_resolved_dependency_rows(
    board: TaskBoard,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Already completed work",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready to run.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.complete_task(blocker["id"])
    event_bus.events.clear()
    original_execute = board._db.execute

    async def execute_with_gap_intake(sql: str, params: tuple = ()) -> None:
        await original_execute(sql, params)
        if sql.startswith("INSERT INTO tasks ") and len(params) >= 4:
            if params[3] == "Gap dependent":
                await board.apply_backlog_intake_decision(
                    params[0],
                    needs_review=False,
                    reason="Intake observed dependency creation gap.",
                )

    monkeypatch.setattr(board._db, "execute", execute_with_gap_intake)

    dependent = await board.create_task(
        group_id=group["id"],
        title="Gap dependent",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )

    assert dependent["status"] == "pending"
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": dependent["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_dependent_created_after_blocker_failed_is_failed(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Failed blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready to run.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.fail_task(blocker["id"])

    dependent = await board.create_task(
        group_id=group["id"],
        title="Depends on failed work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )

    assert dependent["status"] == "failed"
    assert await board.claim_task("tester", "tester-1") is None


async def test_dependent_created_after_blocker_rejected_is_failed(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Rejected blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=True,
        reason="Risky implementation.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    completed = await board.complete_task(blocker["id"])
    assert completed["status"] == "review"
    await board.reject_review_gate(
        blocker["id"],
        reason="Review rejected the implementation.",
    )

    dependent = await board.create_task(
        group_id=group["id"],
        title="Depends on rejected work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    after_intake = await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Should not wait on rejected work.",
    )

    assert dependent["status"] == "failed"
    assert after_intake["status"] == "failed"
    assert after_intake["status"] != "blocked"
    assert await board.claim_task("tester", "tester-1") is None


async def test_create_task_returns_blocked_after_gap_intake_with_unresolved_dependency(
    board: TaskBoard,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Unfinished blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    original_execute = board._db.execute

    async def execute_with_gap_intake(sql: str, params: tuple = ()) -> None:
        await original_execute(sql, params)
        if sql.startswith("INSERT INTO tasks ") and len(params) >= 4:
            if params[3] == "Unresolved gap dependent":
                await board.apply_backlog_intake_decision(
                    params[0],
                    needs_review=False,
                    reason="Intake observed dependency creation gap.",
                )

    monkeypatch.setattr(board._db, "execute", execute_with_gap_intake)

    dependent = await board.create_task(
        group_id=group["id"],
        title="Unresolved gap dependent",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    stored = await board.get_task(dependent["id"])

    assert dependent["status"] == "blocked"
    assert stored["status"] == "blocked"


async def test_create_task_reconciles_blocker_completed_during_dependency_insert(
    board: TaskBoard,
    event_bus: RecordingEventBus,
    monkeypatch: pytest.MonkeyPatch,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Completes during insert",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    event_bus.events.clear()
    original_execute = board._db.execute
    completed_blocker = False

    async def execute_with_completion_race(sql: str, params: tuple = ()) -> None:
        nonlocal completed_blocker
        if sql.startswith("INSERT INTO task_dependencies") and not completed_blocker:
            completed_blocker = True
            await board.complete_task(blocker["id"])
        await original_execute(sql, params)

    monkeypatch.setattr(board._db, "execute", execute_with_completion_race)

    dependent = await board.create_task(
        group_id=group["id"],
        title="Dependent after completion race",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[blocker["id"]],
    )
    updated = await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Dependency completed during creation.",
    )

    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 1}]
    assert updated["status"] == "pending"
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": dependent["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_create_review_revision_tasks_blocks_original_on_revision(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build reviewed widget",
        task_type="implementation",
        assigned_to="coder",
        priority="high",
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == original["id"]
    completed = await board.complete_task_with_output(original["id"], "Ready.")
    assert completed["status"] == "review"

    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )

    assert len(revisions) == 1
    revision = revisions[0]
    assert revision["status"] == "backlog"
    assert revision["review_parent_task_id"] == original["id"]
    assert revision["revision_of"] == original["id"]
    assert revision["parent_id"] == original["id"]
    assert revision["group_id"] == group["id"]
    assert revision["created_by"] == "system"
    assert revision["task_type"] == "revision"
    assert revision["assigned_to"] == "coder"
    assert revision["priority"] == "high"

    parent = await board.get_task(original["id"])
    assert parent["status"] == "review"
    assert parent["review_status"] == "waiting_revision"
    assert json.loads(parent["revision_task_ids"]) == [revision["id"]]
    gate_runs = json.loads(parent["system_gate_runs"])
    assert gate_runs[-1]["gate"] == "review"
    assert gate_runs[-1]["outcome"] == "needs_revision"
    assert gate_runs[-1]["revision_task_ids"] == [revision["id"]]
    assert gate_runs[-1]["finished_at"]

    deps = await board._db.execute_fetchall(
        "SELECT task_id, blocked_by, resolved FROM task_dependencies WHERE task_id = ?",
        (original["id"],),
    )
    assert deps == [
        {"task_id": original["id"], "blocked_by": revision["id"], "resolved": 0}
    ]


async def test_mark_review_ready_if_unblocked_waits_for_revision_dependencies(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build reviewed widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]

    still_waiting = await board.mark_review_ready_if_unblocked(original["id"])

    assert still_waiting["status"] == "review"
    assert still_waiting["review_status"] == "waiting_revision"

    await board.apply_backlog_intake_decision(
        revision["id"],
        needs_review=True,
        reason="Revision children complete directly.",
    )
    claimed_revision = await board.claim_task("coder", "coder-1")
    assert claimed_revision["id"] == revision["id"]
    completed_revision = await board.complete_task(claimed_revision["id"])

    assert completed_revision["status"] == "completed"
    assert completed_revision["review_status"] is None
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ? AND blocked_by = ?",
        (original["id"], revision["id"]),
    )
    assert deps == [{"resolved": 1}]

    ready = await board.mark_review_ready_if_unblocked(original["id"])

    assert ready["status"] == "review"
    assert ready["review_status"] == "pending"


async def test_approve_review_gate_waits_for_revision_dependencies(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[original["id"]],
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    event_bus.events.clear()

    approved = await board.approve_review_gate(
        original["id"],
        reason="Must not approve while revisions are open.",
    )

    assert approved["status"] == "review"
    assert approved["review_status"] == "waiting_revision"
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "blocked"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 0}]
    assert event_bus.events == []


async def test_create_review_revision_tasks_rejects_empty_revisions(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build reviewed widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == original["id"]
    before = await board.complete_task_with_output(original["id"], "Ready.")

    with pytest.raises(ValueError):
        await board.create_review_revision_tasks(original["id"], [])

    after = await board.get_task(original["id"])
    assert after["status"] == before["status"]
    assert after["review_status"] == before["review_status"]
    assert json.loads(after["revision_task_ids"]) == []
    assert json.loads(after["system_gate_runs"]) == []


async def test_add_dependency_marks_completed_blocker_resolved(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Completed blocker",
        task_type="implementation",
        assigned_to="coder",
    )
    target = await board.create_task(
        group_id=group["id"],
        title="Blocked target",
        task_type="qa_verification",
        assigned_to="tester",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.complete_task(blocker["id"])
    await board._db.execute(
        "UPDATE tasks SET status = 'blocked' WHERE id = ?",
        (target["id"],),
    )
    event_bus.events.clear()

    await board.add_dependency(target["id"], blocker["id"])

    deps = await board._db.execute_fetchall(
        "SELECT resolved, resolved_at FROM task_dependencies WHERE task_id = ?",
        (target["id"],),
    )
    assert len(deps) == 1
    assert deps[0]["resolved"] == 1
    assert deps[0]["resolved_at"]
    stored_target = await board.get_task(target["id"])
    assert stored_target["status"] == "pending"
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": target["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_add_dependency_fails_target_for_rejected_blocker(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Rejected blocker",
        task_type="implementation",
        assigned_to="coder",
    )
    target = await board.create_task(
        group_id=group["id"],
        title="Blocked target",
        task_type="qa_verification",
        assigned_to="tester",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.complete_task(blocker["id"])
    await board.reject_review_gate(
        blocker["id"],
        reason="Review rejected the implementation.",
    )
    await board._db.execute(
        "UPDATE tasks SET status = 'blocked' WHERE id = ?",
        (target["id"],),
    )

    await board.add_dependency(target["id"], blocker["id"])

    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (target["id"],),
    )
    assert deps == [{"resolved": 0}]
    stored_target = await board.get_task(target["id"])
    assert stored_target["status"] == "failed"


async def test_failed_revision_rejects_waiting_review_parent(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[original["id"]],
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    claimed_original = await board.claim_task("coder", "coder-1")
    assert claimed_original["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]
    await board.apply_backlog_intake_decision(
        revision["id"],
        needs_review=False,
        reason="Revision children complete directly.",
    )
    claimed_revision = await board.claim_task("coder", "coder-1")
    assert claimed_revision["id"] == revision["id"]
    event_bus.events.clear()

    await board.fail_task(revision["id"])

    parent = await board.get_task(original["id"])
    assert parent["status"] == "rejected"
    assert parent["review_status"] == "rejected"
    assert "required revision" in parent["rejection_reason"].lower()
    assert revision["id"] in parent["rejection_reason"]
    gate_runs = json.loads(parent["system_gate_runs"])
    assert gate_runs[-1]["gate"] == "review"
    assert gate_runs[-1]["outcome"] == "rejected"
    assert gate_runs[-1]["failed_revision_task_id"] == revision["id"]
    assert gate_runs[-1]["finished_at"]

    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "failed"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 0}]
    assert event_bus.events == []


async def test_rejected_revision_rejects_waiting_review_parent(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build reviewed widget",
        task_type="implementation",
        assigned_to="coder",
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed_original = await board.claim_task("coder", "coder-1")
    assert claimed_original["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]

    await board.reject_task(revision["id"], "Revision work was rejected.")

    parent = await board.get_task(original["id"])
    assert parent["status"] == "rejected"
    assert parent["review_status"] == "rejected"
    assert "required revision" in parent["rejection_reason"].lower()
    assert revision["id"] in parent["rejection_reason"]
    gate_runs = json.loads(parent["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "rejected"
    assert gate_runs[-1]["failed_revision_task_id"] == revision["id"]


async def test_add_dependency_fails_target_for_failed_blocker(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Failed blocker",
        task_type="implementation",
        assigned_to="coder",
    )
    target = await board.create_task(
        group_id=group["id"],
        title="Blocked target",
        task_type="qa_verification",
        assigned_to="tester",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed["id"] == blocker["id"]
    await board.fail_task(blocker["id"])
    await board._db.execute(
        "UPDATE tasks SET status = 'blocked' WHERE id = ?",
        (target["id"],),
    )

    await board.add_dependency(target["id"], blocker["id"])

    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (target["id"],),
    )
    assert deps == [{"resolved": 0}]
    stored_target = await board.get_task(target["id"])
    assert stored_target["status"] == "failed"


async def test_add_dependency_rejects_review_pending_target_for_failed_blocker(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    review_task = await board.create_task(
        group_id=group["id"],
        title="Build reviewed widget",
        task_type="implementation",
        assigned_to="coder",
    )
    blocker = await board.create_task(
        group_id=group["id"],
        title="Failed blocker",
        task_type="implementation",
        assigned_to="tester",
    )
    await board.apply_backlog_intake_decision(
        review_task["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready.",
    )
    claimed_review = await board.claim_task("coder", "coder-1")
    assert claimed_review["id"] == review_task["id"]
    await board.complete_task_with_output(review_task["id"], "Ready.")
    claimed_blocker = await board.claim_task("tester", "tester-1")
    assert claimed_blocker["id"] == blocker["id"]
    await board.fail_task(blocker["id"])

    await board.add_dependency(review_task["id"], blocker["id"])

    stored_review = await board.get_task(review_task["id"])
    assert stored_review["status"] == "rejected"
    assert stored_review["review_status"] == "rejected"
    assert "terminal dependency" in stored_review["rejection_reason"].lower()
    assert blocker["id"] in stored_review["rejection_reason"]
    gate_runs = json.loads(stored_review["system_gate_runs"])
    assert gate_runs[-1]["gate"] == "review"
    assert gate_runs[-1]["outcome"] == "rejected"
    assert gate_runs[-1]["failed_dependency_task_id"] == blocker["id"]
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (review_task["id"],),
    )
    assert deps == [{"resolved": 0}]


async def test_add_dependency_rejects_waiting_review_target_for_rejected_blocker(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    review_task = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[review_task["id"]],
    )
    blocker = await board.create_task(
        group_id=group["id"],
        title="Rejected blocker",
        task_type="implementation",
        assigned_to="tester",
    )
    await board.apply_backlog_intake_decision(
        review_task["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    claimed_review = await board.claim_task("coder", "coder-1")
    assert claimed_review["id"] == review_task["id"]
    await board.complete_task_with_output(review_task["id"], "Ready.")
    await board.create_review_revision_tasks(
        review_task["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    claimed_blocker = await board.claim_task("tester", "tester-1")
    assert claimed_blocker["id"] == blocker["id"]
    await board.complete_task(claimed_blocker["id"])
    await board.reject_review_gate(
        blocker["id"],
        reason="Review rejected the blocker.",
    )
    event_bus.events.clear()

    await board.add_dependency(review_task["id"], blocker["id"])

    stored_review = await board.get_task(review_task["id"])
    assert stored_review["status"] == "rejected"
    assert stored_review["review_status"] == "rejected"
    assert "terminal dependency" in stored_review["rejection_reason"].lower()
    assert blocker["id"] in stored_review["rejection_reason"]
    gate_runs = json.loads(stored_review["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "rejected"
    assert gate_runs[-1]["failed_dependency_task_id"] == blocker["id"]

    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "failed"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ?",
        (dependent["id"],),
    )
    assert deps == [{"resolved": 0}]
    assert event_bus.events == []


async def test_completed_revision_makes_parent_review_approvable(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[original["id"]],
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    claimed_original = await board.claim_task("coder", "coder-1")
    assert claimed_original["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]
    await board.apply_backlog_intake_decision(
        revision["id"],
        needs_review=False,
        reason="Revision children complete directly.",
    )
    claimed_revision = await board.claim_task("coder", "coder-1")
    assert claimed_revision["id"] == revision["id"]
    event_bus.events.clear()

    await board.complete_task(revision["id"])

    parent_after_revision = await board.get_task(original["id"])
    assert parent_after_revision["status"] == "review"
    assert parent_after_revision["review_status"] == "pending"
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "blocked"
    assert event_bus.events == []

    approved = await board.approve_review_gate(
        original["id"],
        reason="Revision satisfies the review request.",
    )

    assert approved["status"] == "completed"
    assert approved["review_status"] == "approved"
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "pending"
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": dependent["id"], "role": "tester", "group_id": group["id"]},
        )
    ]


async def test_recover_stale_revision_does_not_ready_review_parent(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[original["id"]],
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    claimed_original = await board.claim_task("coder", "coder-1")
    assert claimed_original["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]
    await board.apply_backlog_intake_decision(
        revision["id"],
        needs_review=False,
        reason="Revision children complete directly.",
    )
    claimed_revision = await board.claim_task("coder", "revision-worker-1")
    assert claimed_revision["id"] == revision["id"]
    event_bus.events.clear()

    recovered = await board.recover_stale_in_progress_tasks(["revision-worker-1"])

    assert [task["id"] for task in recovered] == [revision["id"]]
    stored_revision = await board.get_task(revision["id"])
    assert stored_revision["status"] == "pending"
    assert stored_revision["claimed_by"] is None
    parent = await board.get_task(original["id"])
    assert parent["status"] == "review"
    assert parent["review_status"] == "waiting_revision"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ? AND blocked_by = ?",
        (original["id"], revision["id"]),
    )
    assert deps == [{"resolved": 0}]

    approved = await board.approve_review_gate(
        original["id"],
        reason="Must not approve unfinished recovered revision.",
    )

    assert approved["status"] == "review"
    assert approved["review_status"] == "waiting_revision"
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "blocked"
    assert event_bus.events == []


async def test_recover_stuck_review_parent_after_completed_revision(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[original["id"]],
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    claimed_original = await board.claim_task("coder", "coder-1")
    assert claimed_original["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]
    await board._db.execute(
        "UPDATE tasks SET status = 'completed' WHERE id = ?",
        (revision["id"],),
    )
    event_bus.events.clear()

    repaired = await board.recover_stuck_blocked_tasks()

    assert [task["id"] for task in repaired] == [original["id"]]
    parent = await board.get_task(original["id"])
    assert parent["status"] == "review"
    assert parent["review_status"] == "pending"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ? AND blocked_by = ?",
        (original["id"], revision["id"]),
    )
    assert deps == [{"resolved": 1}]
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "blocked"
    assert event_bus.events == []


async def test_recover_stuck_review_parent_after_failed_revision(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    original = await board.create_task(
        group_id=group["id"],
        title="Build risky foundation",
        task_type="implementation",
        assigned_to="coder",
    )
    dependent = await board.create_task(
        group_id=group["id"],
        title="Verify risky foundation",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[original["id"]],
    )
    await board.apply_backlog_intake_decision(
        original["id"],
        needs_review=True,
        reason="Needs system review.",
    )
    await board.apply_backlog_intake_decision(
        dependent["id"],
        needs_review=False,
        reason="Waits for approved implementation.",
    )
    claimed_original = await board.claim_task("coder", "coder-1")
    assert claimed_original["id"] == original["id"]
    await board.complete_task_with_output(original["id"], "Ready.")
    revisions = await board.create_review_revision_tasks(
        original["id"],
        [{"title": "Fix missing edge-case test"}],
    )
    revision = revisions[0]
    await board._db.execute(
        "UPDATE tasks SET status = 'failed' WHERE id = ?",
        (revision["id"],),
    )
    event_bus.events.clear()

    repaired = await board.recover_stuck_blocked_tasks()

    assert [task["id"] for task in repaired] == [original["id"]]
    parent = await board.get_task(original["id"])
    assert parent["status"] == "rejected"
    assert parent["review_status"] == "rejected"
    assert "required revision" in parent["rejection_reason"].lower()
    assert revision["id"] in parent["rejection_reason"]
    gate_runs = json.loads(parent["system_gate_runs"])
    assert gate_runs[-1]["outcome"] == "rejected"
    assert gate_runs[-1]["failed_revision_task_id"] == revision["id"]
    stored_dependent = await board.get_task(dependent["id"])
    assert stored_dependent["status"] == "failed"
    assert event_bus.events == []


async def test_add_dependency_failed_blocker_cascades_from_non_review_target(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    failed_blocker = await board.create_task(
        group_id=group["id"],
        title="Failed blocker",
        task_type="implementation",
        assigned_to="coder",
    )
    target = await board.create_task(
        group_id=group["id"],
        title="Target work",
        task_type="implementation",
        assigned_to="tester",
    )
    downstream = await board.create_task(
        group_id=group["id"],
        title="Downstream work",
        task_type="qa_verification",
        assigned_to="tester",
        blocked_by=[target["id"]],
    )
    await board.apply_backlog_intake_decision(
        failed_blocker["id"],
        needs_review=False,
        reason="Ready.",
    )
    await board.apply_backlog_intake_decision(
        target["id"],
        needs_review=False,
        reason="Ready.",
    )
    await board.apply_backlog_intake_decision(
        downstream["id"],
        needs_review=False,
        reason="Waits for target.",
    )
    claimed_blocker = await board.claim_task("coder", "coder-1")
    assert claimed_blocker["id"] == failed_blocker["id"]
    await board.fail_task(failed_blocker["id"])

    await board.add_dependency(target["id"], failed_blocker["id"])

    stored_target = await board.get_task(target["id"])
    assert stored_target["status"] == "failed"
    stored_downstream = await board.get_task(downstream["id"])
    assert stored_downstream["status"] == "failed"


async def test_add_dependency_blocks_pending_target_until_blocker_completes(
    board: TaskBoard,
    event_bus: RecordingEventBus,
):
    group = await board.create_group(title="Feature", created_by="pm")
    blocker = await board.create_task(
        group_id=group["id"],
        title="Blocking work",
        task_type="implementation",
        assigned_to="coder",
    )
    target = await board.create_task(
        group_id=group["id"],
        title="Target work",
        task_type="qa_verification",
        assigned_to="tester",
    )
    await board.apply_backlog_intake_decision(
        blocker["id"],
        needs_review=False,
        reason="Ready.",
    )
    await board.apply_backlog_intake_decision(
        target["id"],
        needs_review=False,
        reason="Ready.",
    )
    event_bus.events.clear()

    await board.add_dependency(target["id"], blocker["id"])

    stored_target = await board.get_task(target["id"])
    assert stored_target["status"] == "blocked"
    deps = await board._db.execute_fetchall(
        "SELECT resolved FROM task_dependencies WHERE task_id = ? AND blocked_by = ?",
        (target["id"], blocker["id"]),
    )
    assert deps == [{"resolved": 0}]
    assert await board.claim_task("tester", "tester-1") is None
    assert event_bus.events == []

    claimed_blocker = await board.claim_task("coder", "coder-1")
    assert claimed_blocker["id"] == blocker["id"]
    await board.complete_task(blocker["id"])

    stored_target = await board.get_task(target["id"])
    assert stored_target["status"] == "pending"
    assert event_bus.events == [
        (
            "task.available",
            {"task_id": target["id"], "role": "tester", "group_id": group["id"]},
        )
    ]
    claimed_target = await board.claim_task("tester", "tester-1")
    assert claimed_target["id"] == target["id"]
