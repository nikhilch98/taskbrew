"""Tests for Work Package persistence and task linkage."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import DEFAULT_MAX_REVIEW_ROUNDS, TaskBoard


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
async def board(db: Database) -> TaskBoard:
    task_board = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
        event_bus=RecordingEventBus(),
    )
    await task_board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    return task_board


async def test_schema_has_work_package_tables(db: Database):
    tables = {
        row["name"]
        for row in await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert "milestones" in tables
    assert "work_packages" in tables
    assert "review_gates" in tables


async def test_tasks_schema_has_package_columns(db: Database):
    columns = {
        row["name"]
        for row in await db.execute_fetchall("PRAGMA table_info(tasks)")
    }

    assert {"work_package_id", "milestone_id", "review_scope"}.issubset(columns)


async def test_create_work_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    package = await board.create_work_package(
        group_id=group["id"],
        title="Implement dashboard live updates",
        description="Deliver websocket-driven board refresh.",
        created_by="architect-1",
        risk_level="medium",
    )

    assert package["id"].startswith("WP-")
    assert package["group_id"] == group["id"]
    assert package["title"] == "Implement dashboard live updates"
    assert package["status"] == "pending"
    assert package["review_scope"] == "work_package"
    assert package["review_status"] is None


async def test_create_task_assigns_default_work_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )

    assert task["work_package_id"].startswith("WP-")
    package = await board.get_work_package(task["work_package_id"])
    assert package["title"] == "Default package for Feature"
    assert package["group_id"] == group["id"]


async def test_create_task_ignores_manual_package_for_default(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    manual_package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )

    assert task["work_package_id"] != manual_package["id"]
    package = await board.get_work_package(task["work_package_id"])
    assert package["title"] == "Default package for Feature"
    assert package["group_id"] == group["id"]
    assert package["created_by"] == "system"


async def test_package_status_tracks_child_task_progress(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
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
    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"

    await board.complete_task_with_output(task["id"], "Done.")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "review"
    assert package["review_status"] == "pending"


async def test_failed_child_blocks_package_review(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
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

    await board.fail_task(task["id"], reason="Tests failed")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "blocked"
    assert package["review_status"] is None


async def test_unblocked_child_reconciles_its_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package_a = await board.create_work_package(
        group_id=group["id"],
        title="Package A",
        created_by="architect-1",
    )
    package_b = await board.create_work_package(
        group_id=group["id"],
        title="Package B",
        created_by="architect-1",
    )
    task_a = await board.create_task(
        group_id=group["id"],
        title="Build foundation",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package_a["id"],
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Build dependent widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package_b["id"],
        blocked_by=[task_a["id"]],
    )

    await board.apply_backlog_intake_decision(
        task_a["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    await board.apply_backlog_intake_decision(
        task_b["id"],
        needs_review=False,
        reason="Covered by package review.",
    )

    package_b = await board.get_work_package(package_b["id"])
    assert package_b["status"] == "blocked"

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task_a["id"]

    await board.complete_task_with_output(task_a["id"], "Done.")

    task_b = await board.get_task(task_b["id"])
    package_b = await board.get_work_package(package_b["id"])
    assert task_b["status"] == "pending"
    assert package_b["status"] == "pending"


async def test_group_stays_active_while_package_review_pending(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
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

    await board.complete_task_with_output(task["id"], "Done.")

    package = await board.get_work_package(package["id"])
    groups = await board.get_groups()
    completed_group = next(item for item in groups if item["id"] == group["id"])
    assert package["status"] == "review"
    assert package["review_status"] == "pending"
    assert completed_group["status"] == "active"
    assert completed_group["completed_at"] is None


async def test_in_progress_child_wins_over_pending_package_status(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task_a = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Build another widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    for task in (task_a, task_b):
        await board.apply_backlog_intake_decision(
            task["id"],
            needs_review=False,
            reason="Covered by package review.",
        )

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task_a["id"]

    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"


async def test_ensure_review_gate_defaults_and_is_idempotent(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    gate = await board.ensure_review_gate(
        entity_type="work_package",
        entity_id="WP-test",
        group_id=group["id"],
    )
    same_gate = await board.ensure_review_gate(
        entity_type="work_package",
        entity_id="WP-test",
        group_id=group["id"],
    )

    assert gate["id"] == same_gate["id"]
    assert gate["max_review_rounds"] == DEFAULT_MAX_REVIEW_ROUNDS


async def test_reject_task_reconciles_package_before_group_completion(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    rejected = await board.reject_task(task["id"], "No longer needed.")

    package = await board.get_work_package(package["id"])
    groups = await board.get_groups()
    completed_group = next(item for item in groups if item["id"] == group["id"])
    assert rejected["status"] == "rejected"
    assert package["status"] == "rejected"
    assert completed_group["status"] == "completed"
    assert completed_group["completed_at"] is not None


async def test_completed_no_review_package_reopens_for_new_child_work(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
        review_scope="none",
    )
    first_task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        first_task["id"],
        needs_review=False,
        reason="No package review needed.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == first_task["id"]
    await board.complete_task_with_output(first_task["id"], "Done.")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "completed"
    assert package["review_status"] == "skipped"

    second_task = await board.create_task(
        group_id=group["id"],
        title="Build follow-up widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    package = await board.get_work_package(package["id"])
    assert second_task["status"] == "backlog"
    assert package["status"] == "pending"
    assert package["review_status"] is None

    await board.apply_backlog_intake_decision(
        second_task["id"],
        needs_review=False,
        reason="No package review needed.",
    )
    claimed = await board.claim_task("coder", "coder-2")

    assert claimed is not None
    assert claimed["id"] == second_task["id"]
    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"
    assert package["review_status"] is None


async def test_work_package_board_surfaces_review_gate_attention_metadata(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Reviewable package",
        created_by="architect-1",
        risk_level="high",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build reviewable widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(claimed["id"], "Done.")

    board_data = await board.get_work_package_board(group_id=group["id"])
    card = next(pkg for pkg in board_data["packages"] if pkg["id"] == package["id"])

    assert card["status"] == "review"
    assert card["task_counts"]["total"] == 1
    assert card["review_gate"]["entity_id"] == package["id"]
    assert card["review_gate"]["status"] == "pending"
    assert card["needs_attention"] is True
    assert any(reason["type"] == "review_gate" for reason in card["attention_reasons"])
    assert card["waiting_age_seconds"] >= 0
    assert "latest_review_reason_summary" in card


async def test_work_package_detail_includes_tasks_review_gate_and_timeline(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Detailed package",
        description="Package detail should expose review context.",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build detailed widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(claimed["id"], "Done.")

    detail = await board.get_work_package_detail(package["id"])

    assert detail is not None
    assert detail["id"] == package["id"]
    assert detail["tasks"][0]["id"] == task["id"]
    assert detail["review_gate"]["entity_id"] == package["id"]
    assert isinstance(detail["review_runs"], list)
    assert "artifacts" in detail
    assert any(item["type"] == "package.created" for item in detail["timeline"])


async def test_operations_summary_classifies_package_review_queue(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Queued package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build queued widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(claimed["id"], "Done.")

    summary = await board.get_operations_summary(group_id=group["id"])

    assert summary["counts"]["packages_total"] == 1
    assert summary["counts"]["packages_review"] == 1
    assert summary["counts"]["packages_attention"] == 1
    assert summary["system_agent"]["status"] == "idle"
    assert summary["queues"]["review"][0]["id"] == package["id"]
    assert summary["queues"]["attention"][0]["id"] == package["id"]
