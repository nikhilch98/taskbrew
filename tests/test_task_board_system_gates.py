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
