"""Tests for the MigrationManager."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.migration import MigrationManager, MIGRATIONS


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def migration_mgr(db: Database) -> MigrationManager:
    """Create a MigrationManager backed by the in-memory database."""
    return MigrationManager(db)


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_get_current_version(migration_mgr: MigrationManager):
    """get_current_version returns the highest applied migration version."""
    version = await migration_mgr.get_current_version()
    # All built-in migrations are applied during initialize()
    assert version == len(MIGRATIONS)


async def test_apply_pending(migration_mgr: MigrationManager, db: Database):
    """apply_pending applies new migrations and version increases."""
    current = await migration_mgr.get_current_version()

    # Add test migrations beyond the existing ones
    test_migrations = list(MIGRATIONS) + [
        (current + 1, "create_test_table", "CREATE TABLE test_migration (id TEXT PRIMARY KEY, val TEXT);"),
        (current + 2, "add_test_column", "ALTER TABLE test_migration ADD COLUMN extra TEXT;"),
    ]

    with patch("taskbrew.orchestrator.migration.MIGRATIONS", test_migrations):
        applied = await migration_mgr.apply_pending()

    assert len(applied) == 2
    assert applied[0] == "create_test_table"
    assert applied[1] == "add_test_column"

    # Version should now be current + 2
    version = await migration_mgr.get_current_version()
    assert version == current + 2

    # Running apply_pending again should apply nothing
    with patch("taskbrew.orchestrator.migration.MIGRATIONS", test_migrations):
        applied_again = await migration_mgr.apply_pending()
    assert len(applied_again) == 0

    # Verify the test table actually exists
    rows = await db.execute_fetchall("SELECT * FROM test_migration")
    assert rows == []  # Empty table but it exists (no error)
