"""Tests for the Database class."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.database import Database


@pytest.fixture
async def db():
    """Create an in-memory database, initialise it, and tear it down after the test."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


# ------------------------------------------------------------------
# Schema
# ------------------------------------------------------------------


async def test_initialize_creates_tables(db: Database):
    """All 7 expected tables must be present after initialize()."""
    rows = await db.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = {r["name"] for r in rows}
    expected = {
        "groups",
        "tasks",
        "task_dependencies",
        "artifacts",
        "agent_instances",
        "id_sequences",
        "events",
        "task_usage",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


# ------------------------------------------------------------------
# ID generation
# ------------------------------------------------------------------


async def test_generate_task_id(db: Database):
    """Generating IDs should return sequential, zero-padded values."""
    await db.register_prefix("CD")
    first = await db.generate_task_id("CD")
    second = await db.generate_task_id("CD")
    assert first == "CD-001"
    assert second == "CD-002"


async def test_generate_task_id_unregistered(db: Database):
    """Requesting an ID for an unknown prefix must raise ValueError."""
    with pytest.raises(ValueError, match="Unregistered prefix"):
        await db.generate_task_id("XX")
