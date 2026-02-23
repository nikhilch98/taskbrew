"""Tests for the CostManager."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.cost_manager import CostManager


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
async def cost_mgr(db: Database) -> CostManager:
    """Create a CostManager backed by the in-memory database."""
    return CostManager(db)


# ------------------------------------------------------------------
# Budget CRUD Tests
# ------------------------------------------------------------------


async def test_check_budget_no_budgets(cost_mgr: CostManager):
    """check_budget returns allowed=True when no budgets exist."""
    result = await cost_mgr.check_budget()
    assert result["allowed"] is True
    assert result["remaining"] is None
    assert result["scope"] is None


async def test_create_budget(cost_mgr: CostManager):
    """create_budget creates a global budget with correct fields."""
    budget = await cost_mgr.create_budget(scope="global", budget_usd=100.0, period="daily")

    assert budget["scope"] == "global"
    assert budget["budget_usd"] == 100.0
    assert budget["period"] == "daily"
    assert "id" in budget

    # Verify it shows up in get_budgets
    budgets = await cost_mgr.get_budgets()
    assert len(budgets) == 1
    assert budgets[0]["scope"] == "global"
    assert budgets[0]["budget_usd"] == 100.0


async def test_create_role_budget(cost_mgr: CostManager):
    """create_budget with scope='role' sets scope_id correctly."""
    budget = await cost_mgr.create_budget(
        scope="role", budget_usd=25.0, scope_id="coder", period="weekly"
    )
    assert budget["scope"] == "role"
    assert budget["scope_id"] == "coder"
    assert budget["budget_usd"] == 25.0
    assert budget["period"] == "weekly"


async def test_create_group_budget(cost_mgr: CostManager):
    """create_budget with scope='group' sets scope_id correctly."""
    budget = await cost_mgr.create_budget(
        scope="group", budget_usd=50.0, scope_id="FEAT-001", period="monthly"
    )
    assert budget["scope"] == "group"
    assert budget["scope_id"] == "FEAT-001"
    assert budget["budget_usd"] == 50.0
    assert budget["period"] == "monthly"


async def test_record_spend_updates_budget(cost_mgr: CostManager):
    """record_spend updates the spent_usd field of applicable budgets."""
    await cost_mgr.create_budget(scope="global", budget_usd=100.0, period="daily")

    await cost_mgr.record_spend(25.0)

    budgets = await cost_mgr.get_budgets()
    assert budgets[0]["spent_usd"] == 25.0

    await cost_mgr.record_spend(10.0)

    budgets = await cost_mgr.get_budgets()
    assert budgets[0]["spent_usd"] == 35.0


async def test_record_spend_updates_role_budget(cost_mgr: CostManager):
    """record_spend with role updates both global and role budgets."""
    await cost_mgr.create_budget(scope="global", budget_usd=200.0, period="daily")
    await cost_mgr.create_budget(scope="role", budget_usd=50.0, scope_id="coder", period="daily")

    await cost_mgr.record_spend(10.0, role="coder")

    budgets = await cost_mgr.get_budgets()
    global_b = next(b for b in budgets if b["scope"] == "global")
    role_b = next(b for b in budgets if b["scope"] == "role")
    assert global_b["spent_usd"] == 10.0
    assert role_b["spent_usd"] == 10.0


async def test_budget_exceeded_blocks(cost_mgr: CostManager):
    """After spending over budget, check_budget returns allowed=False."""
    await cost_mgr.create_budget(scope="global", budget_usd=50.0, period="daily")

    await cost_mgr.record_spend(50.0)

    result = await cost_mgr.check_budget()
    assert result["allowed"] is False
    assert result["scope"] == "global"
    assert result["spent"] == 50.0
    assert result["budget"] == 50.0
    assert result["remaining"] == 0


async def test_role_budget_exceeded_blocks(cost_mgr: CostManager):
    """Role budget exceeded blocks even when global is still OK."""
    await cost_mgr.create_budget(scope="global", budget_usd=1000.0, period="daily")
    await cost_mgr.create_budget(scope="role", budget_usd=10.0, scope_id="coder", period="daily")

    await cost_mgr.record_spend(10.0, role="coder")

    result = await cost_mgr.check_budget(role="coder")
    assert result["allowed"] is False
    assert result["scope"] == "role"


# ------------------------------------------------------------------
# Threshold Notification Tests
# ------------------------------------------------------------------


async def test_threshold_notification_created(cost_mgr: CostManager, db: Database):
    """Spending past 80% threshold creates a budget_warning notification."""
    await cost_mgr.create_budget(scope="global", budget_usd=10.0, period="daily")

    # Spend 85% of the budget
    await cost_mgr.record_spend(8.5)

    notifications = await db.get_unread_notifications()
    assert len(notifications) == 1
    assert notifications[0]["type"] == "budget_warning"
    assert notifications[0]["severity"] == "warning"
    assert "85%" in notifications[0]["message"]


async def test_critical_notification_at_100pct(cost_mgr: CostManager, db: Database):
    """Spending at 100% creates a critical severity notification."""
    await cost_mgr.create_budget(scope="global", budget_usd=10.0, period="daily")

    await cost_mgr.record_spend(10.0)

    notifications = await db.get_unread_notifications()
    assert len(notifications) == 1
    assert notifications[0]["severity"] == "critical"


# ------------------------------------------------------------------
# Duplicate Notification Prevention Tests
# ------------------------------------------------------------------


async def test_dedup_notifications(cost_mgr: CostManager, db: Database):
    """Recording spend twice within 1 hour only creates 1 notification."""
    await cost_mgr.create_budget(scope="global", budget_usd=10.0, period="daily")

    # First spend exceeds 80% threshold
    await cost_mgr.record_spend(9.0)

    # Second spend still over threshold but within 1 hour dedup window
    await cost_mgr.record_spend(0.5)

    notifications = await db.get_unread_notifications()
    # Should have only 1 notification due to dedup
    assert len(notifications) == 1
    assert "budget" in notifications[0]["type"].lower() or "budget" in notifications[0]["title"].lower()


async def test_dedup_allows_after_read_notification(cost_mgr: CostManager, db: Database):
    """After marking a notification as read, a new one should be created on
    the next threshold breach because the dedup only checks unread notifications."""
    await cost_mgr.create_budget(scope="global", budget_usd=10.0, period="daily")

    # First spend creates a notification
    await cost_mgr.record_spend(9.0)

    notifications = await db.get_unread_notifications()
    assert len(notifications) == 1

    # Mark it as read (user dismissed the warning)
    await db.mark_notification_read(notifications[0]["id"])

    # Spend again -- the notification was read, so a new one should be created
    await cost_mgr.record_spend(0.5)

    all_notifs = await db.execute_fetchall(
        "SELECT * FROM notifications WHERE type = 'budget_warning'"
    )
    assert len(all_notifs) == 2  # Old (read) + new notification


async def test_dedup_blocks_while_unread(cost_mgr: CostManager, db: Database):
    """An unread notification prevents duplicates regardless of age."""
    await cost_mgr.create_budget(scope="global", budget_usd=10.0, period="daily")

    # First spend creates a notification
    await cost_mgr.record_spend(9.0)

    # Manually backdate the notification to be >1 hour old but keep it unread
    two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    await db.execute(
        "UPDATE notifications SET created_at = ? WHERE type = 'budget_warning'",
        (two_hours_ago,),
    )

    # Spend again -- notification is old but still unread, so should be deduped
    await cost_mgr.record_spend(0.5)

    all_notifs = await db.execute_fetchall(
        "SELECT * FROM notifications WHERE type = 'budget_warning'"
    )
    assert len(all_notifs) == 1  # Still only 1 because it's unread


# ------------------------------------------------------------------
# Delete Budget Tests
# ------------------------------------------------------------------


async def test_delete_budget(cost_mgr: CostManager):
    """Budget no longer exists after delete."""
    budget = await cost_mgr.create_budget(scope="global", budget_usd=100.0, period="daily")

    await cost_mgr.delete_budget(budget["id"])

    budgets = await cost_mgr.get_budgets()
    assert len(budgets) == 0


async def test_delete_nonexistent_budget(cost_mgr: CostManager):
    """Deleting a budget that doesn't exist is a no-op (no error)."""
    await cost_mgr.delete_budget("nonexistent-id")
    budgets = await cost_mgr.get_budgets()
    assert len(budgets) == 0
