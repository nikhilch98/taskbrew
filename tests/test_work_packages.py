"""Tests for Work Package persistence and task linkage."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


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
