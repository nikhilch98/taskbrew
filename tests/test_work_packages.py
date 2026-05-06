"""Tests for Work Package persistence and task linkage."""

from __future__ import annotations

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
