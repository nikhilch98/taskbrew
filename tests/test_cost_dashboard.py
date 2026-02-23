"""Tests for the cost dashboard API endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager


@pytest.fixture
async def cost_client(tmp_path):
    """Create a test app client with cost budgets seeded."""
    db = Database(str(tmp_path / "test_costs.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "db": db, "board": board}
    await db.close()


# ------------------------------------------------------------------
# Summary endpoint
# ------------------------------------------------------------------


async def test_cost_summary_empty(cost_client):
    """Summary returns empty budgets when none exist."""
    resp = await cost_client["client"].get("/api/costs/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["budgets"] == []
    assert data["total_budget_usd"] == 0
    assert data["total_spent_usd"] == 0
    assert data["total_utilization_pct"] == 0


async def test_cost_summary_with_budgets(cost_client):
    """Summary returns utilization for active budgets."""
    db = cost_client["db"]
    now = datetime.now(timezone.utc)
    reset_at = (now + timedelta(days=1)).isoformat()

    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("b1", "global", None, 100.0, 45.0, "daily", reset_at, now.isoformat()),
    )
    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("b2", "role", "coder", 50.0, 40.0, "daily", reset_at, now.isoformat()),
    )

    resp = await cost_client["client"].get("/api/costs/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["budgets"]) == 2

    global_b = next(b for b in data["budgets"] if b["scope"] == "global")
    assert global_b["budget_usd"] == 100.0
    assert global_b["spent_usd"] == 45.0
    assert global_b["remaining_usd"] == 55.0
    assert global_b["utilization_pct"] == 45.0

    role_b = next(b for b in data["budgets"] if b["scope"] == "role")
    assert role_b["scope_id"] == "coder"
    assert role_b["utilization_pct"] == 80.0

    assert data["total_budget_usd"] == 150.0
    assert data["total_spent_usd"] == 85.0


async def test_cost_summary_excludes_expired(cost_client):
    """Summary excludes budgets with reset_at in the past."""
    db = cost_client["db"]
    now = datetime.now(timezone.utc)
    expired = (now - timedelta(hours=1)).isoformat()
    active = (now + timedelta(days=1)).isoformat()

    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("expired", "global", None, 100.0, 90.0, "daily", expired, now.isoformat()),
    )
    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("active", "global", None, 200.0, 50.0, "weekly", active, now.isoformat()),
    )

    resp = await cost_client["client"].get("/api/costs/summary")
    data = resp.json()
    assert len(data["budgets"]) == 1
    assert data["budgets"][0]["id"] == "active"


# ------------------------------------------------------------------
# History endpoint
# ------------------------------------------------------------------


async def test_cost_history_empty(cost_client):
    """History returns empty when no data exists."""
    resp = await cost_client["client"].get("/api/costs/history?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days_requested"] == 7
    # Should return either empty history or budget_snapshot fallback
    assert "history" in data or "current_budgets" in data


async def test_cost_history_with_attributions(cost_client):
    """History returns daily totals when cost_attributions table exists."""
    db = cost_client["db"]
    # Create the cost_attributions table (normally from migration)
    await db.execute(
        "CREATE TABLE IF NOT EXISTS cost_attributions ("
        "id TEXT PRIMARY KEY, task_id TEXT, feature_tag TEXT, "
        "agent_id TEXT NOT NULL, input_tokens INTEGER DEFAULT 0, "
        "output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, "
        "attributed_at TEXT NOT NULL)"
    )
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    for i in range(3):
        await db.execute(
            "INSERT INTO cost_attributions (id, agent_id, cost_usd, input_tokens, output_tokens, attributed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"ca-{i}", "coder", 0.05, 1000, 500, now.isoformat()),
        )

    resp = await cost_client["client"].get("/api/costs/history?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "cost_attributions"
    assert len(data["history"]) >= 1
    day_entry = data["history"][0]
    assert day_entry["date"] == today
    assert day_entry["cost_usd"] == pytest.approx(0.15, abs=0.001)
    assert day_entry["records"] == 3


async def test_cost_history_days_validation(cost_client):
    """History rejects invalid days parameter."""
    resp = await cost_client["client"].get("/api/costs/history?days=0")
    assert resp.status_code == 422

    resp = await cost_client["client"].get("/api/costs/history?days=500")
    assert resp.status_code == 422


# ------------------------------------------------------------------
# By-role endpoint
# ------------------------------------------------------------------


async def test_costs_by_role_empty(cost_client):
    """By-role returns empty when no role data exists."""
    resp = await cost_client["client"].get("/api/costs/by-role")
    assert resp.status_code == 200
    data = resp.json()
    assert data["roles"] == []


async def test_costs_by_role_from_budgets(cost_client):
    """By-role falls back to budget snapshots."""
    db = cost_client["db"]
    now = datetime.now(timezone.utc)
    reset_at = (now + timedelta(days=1)).isoformat()

    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("r1", "role", "coder", 50.0, 20.0, "daily", reset_at, now.isoformat()),
    )
    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("r2", "role", "tester", 30.0, 5.0, "daily", reset_at, now.isoformat()),
    )

    resp = await cost_client["client"].get("/api/costs/by-role")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "budget_snapshot"
    assert len(data["roles"]) == 2
    # Ordered by spent_usd desc
    assert data["roles"][0]["role"] == "coder"
    assert data["roles"][0]["total_cost_usd"] == 20.0


async def test_costs_by_role_from_attributions(cost_client):
    """By-role uses cost_attributions when available."""
    db = cost_client["db"]
    await db.execute(
        "CREATE TABLE IF NOT EXISTS cost_attributions ("
        "id TEXT PRIMARY KEY, task_id TEXT, feature_tag TEXT, "
        "agent_id TEXT NOT NULL, input_tokens INTEGER DEFAULT 0, "
        "output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, "
        "attributed_at TEXT NOT NULL)"
    )
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("a1", "coder", 0.10, 2000, 1000, now),
    )
    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("a2", "coder", 0.05, 1000, 500, now),
    )
    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("a3", "reviewer", 0.03, 500, 250, now),
    )

    resp = await cost_client["client"].get("/api/costs/by-role")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "cost_attributions"
    assert len(data["roles"]) == 2
    coder_entry = next(r for r in data["roles"] if r["role"] == "coder")
    assert coder_entry["total_cost_usd"] == pytest.approx(0.15, abs=0.001)
    assert coder_entry["input_tokens"] == 3000
    assert coder_entry["records"] == 2


# ------------------------------------------------------------------
# By-group endpoint
# ------------------------------------------------------------------


async def test_costs_by_group_empty(cost_client):
    """By-group returns empty when no group data exists."""
    resp = await cost_client["client"].get("/api/costs/by-group")
    assert resp.status_code == 200
    data = resp.json()
    assert data["groups"] == []


async def test_costs_by_group_from_budgets(cost_client):
    """By-group falls back to budget snapshots."""
    db = cost_client["db"]
    now = datetime.now(timezone.utc)
    reset_at = (now + timedelta(days=1)).isoformat()

    await db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("g1", "group", "FEAT-001", 200.0, 75.0, "weekly", reset_at, now.isoformat()),
    )

    resp = await cost_client["client"].get("/api/costs/by-group")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "budget_snapshot"
    assert len(data["groups"]) == 1
    assert data["groups"][0]["group_id"] == "FEAT-001"
    assert data["groups"][0]["total_cost_usd"] == 75.0


async def test_costs_by_group_from_attributions(cost_client):
    """By-group uses cost_attributions with feature_tag."""
    db = cost_client["db"]
    await db.execute(
        "CREATE TABLE IF NOT EXISTS cost_attributions ("
        "id TEXT PRIMARY KEY, task_id TEXT, feature_tag TEXT, "
        "agent_id TEXT NOT NULL, input_tokens INTEGER DEFAULT 0, "
        "output_tokens INTEGER DEFAULT 0, cost_usd REAL DEFAULT 0, "
        "attributed_at TEXT NOT NULL)"
    )
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, feature_tag, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("g1", "coder", "FEAT-001", 0.10, 2000, 1000, now),
    )
    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, feature_tag, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("g2", "tester", "FEAT-001", 0.05, 1000, 500, now),
    )
    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, feature_tag, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("g3", "coder", "FEAT-002", 0.08, 1500, 700, now),
    )
    # One without feature_tag (should be excluded from group breakdown)
    await db.execute(
        "INSERT INTO cost_attributions (id, agent_id, feature_tag, cost_usd, input_tokens, output_tokens, attributed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("g4", "coder", None, 0.02, 300, 100, now),
    )

    resp = await cost_client["client"].get("/api/costs/by-group")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "cost_attributions"
    assert len(data["groups"]) == 2
    feat1 = next(g for g in data["groups"] if g["group_id"] == "FEAT-001")
    assert feat1["total_cost_usd"] == pytest.approx(0.15, abs=0.001)
    assert feat1["records"] == 2


# ------------------------------------------------------------------
# Costs page route
# ------------------------------------------------------------------


async def test_costs_page(cost_client):
    """The /costs page returns HTML."""
    resp = await cost_client["client"].get("/costs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
