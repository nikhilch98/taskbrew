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
