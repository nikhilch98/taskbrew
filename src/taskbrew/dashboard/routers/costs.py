"""Cost dashboard API endpoints for budget utilization, history, and breakdowns."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


def _get_db():
    """Return the database from the orchestrator."""
    orch = get_orch()
    # The DB is accessible via task_board._db (same pattern as system.py budgets)
    db = getattr(orch, "db", None) or getattr(orch.task_board, "_db", None)
    if db is None:
        raise HTTPException(503, "Database unavailable")
    return db


# ------------------------------------------------------------------
# GET /api/costs/summary — Current budget utilization across all scopes
# ------------------------------------------------------------------


@router.get("/api/costs/summary")
async def get_cost_summary():
    """Return current budget utilization for all active budgets."""
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()

    budgets = await db.execute_fetchall(
        "SELECT id, scope, scope_id, budget_usd, spent_usd, period, reset_at, created_at "
        "FROM cost_budgets "
        "WHERE reset_at IS NULL OR reset_at > ? "
        "ORDER BY scope, scope_id",
        (now,),
    )

    results = []
    for b in budgets:
        budget_usd = b["budget_usd"] or 0
        spent_usd = b["spent_usd"] or 0
        remaining = max(0, budget_usd - spent_usd)
        pct = (spent_usd / budget_usd * 100) if budget_usd > 0 else 0

        results.append({
            "id": b["id"],
            "scope": b["scope"],
            "scope_id": b["scope_id"],
            "budget_usd": budget_usd,
            "spent_usd": round(spent_usd, 6),
            "remaining_usd": round(remaining, 6),
            "utilization_pct": round(pct, 1),
            "period": b["period"],
            "reset_at": b["reset_at"],
        })

    total_budget = sum(r["budget_usd"] for r in results)
    total_spent = sum(r["spent_usd"] for r in results)

    return {
        "budgets": results,
        "total_budget_usd": round(total_budget, 2),
        "total_spent_usd": round(total_spent, 6),
        "total_utilization_pct": round(
            (total_spent / total_budget * 100) if total_budget > 0 else 0, 1
        ),
    }


# ------------------------------------------------------------------
# GET /api/costs/history?days=30 — Daily cost totals for past N days
# ------------------------------------------------------------------


@router.get("/api/costs/history")
async def get_cost_history(days: int = Query(default=30, ge=1, le=365)):
    """Return daily cost totals from cost_attributions for the past N days.

    Falls back to budget-based estimates if cost_attributions table
    does not exist (e.g. migrations not run).
    """
    db = _get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Try cost_attributions first (granular per-agent data)
    try:
        rows = await db.execute_fetchall(
            "SELECT DATE(attributed_at) AS day, "
            "SUM(cost_usd) AS total_cost, "
            "SUM(input_tokens) AS total_input_tokens, "
            "SUM(output_tokens) AS total_output_tokens, "
            "COUNT(*) AS record_count "
            "FROM cost_attributions "
            "WHERE attributed_at >= ? "
            "GROUP BY DATE(attributed_at) "
            "ORDER BY day",
            (since,),
        )
        return {
            "days_requested": days,
            "source": "cost_attributions",
            "history": [
                {
                    "date": r["day"],
                    "cost_usd": round(r["total_cost"] or 0, 6),
                    "input_tokens": r["total_input_tokens"] or 0,
                    "output_tokens": r["total_output_tokens"] or 0,
                    "records": r["record_count"] or 0,
                }
                for r in rows
            ],
        }
    except Exception:
        # cost_attributions table may not exist — fall back to budget snapshot
        pass

    # Fallback: return current budget snapshot (no daily granularity available)
    now = datetime.now(timezone.utc).isoformat()
    budgets = await db.execute_fetchall(
        "SELECT scope, scope_id, spent_usd, budget_usd, period, created_at "
        "FROM cost_budgets "
        "WHERE reset_at IS NULL OR reset_at > ? "
        "ORDER BY scope",
        (now,),
    )
    return {
        "days_requested": days,
        "source": "budget_snapshot",
        "history": [],
        "current_budgets": [
            {
                "scope": b["scope"],
                "scope_id": b["scope_id"],
                "spent_usd": round(b["spent_usd"] or 0, 6),
                "budget_usd": b["budget_usd"] or 0,
                "period": b["period"],
            }
            for b in budgets
        ],
    }


# ------------------------------------------------------------------
# GET /api/costs/by-role — Cost breakdown by role
# ------------------------------------------------------------------


@router.get("/api/costs/by-role")
async def get_costs_by_role():
    """Return cost breakdown grouped by role.

    Uses cost_attributions (agent_id maps to role) if available,
    otherwise falls back to role-scoped budgets.
    """
    db = _get_db()

    # Try cost_attributions first (only use if it has data)
    try:
        rows = await db.execute_fetchall(
            "SELECT agent_id AS role, "
            "SUM(cost_usd) AS total_cost, "
            "SUM(input_tokens) AS total_input_tokens, "
            "SUM(output_tokens) AS total_output_tokens, "
            "COUNT(*) AS record_count "
            "FROM cost_attributions "
            "GROUP BY agent_id "
            "ORDER BY SUM(cost_usd) DESC",
        )
        if rows:
            return {
                "source": "cost_attributions",
                "roles": [
                    {
                        "role": r["role"],
                        "total_cost_usd": round(r["total_cost"] or 0, 6),
                        "input_tokens": r["total_input_tokens"] or 0,
                        "output_tokens": r["total_output_tokens"] or 0,
                        "records": r["record_count"] or 0,
                    }
                    for r in rows
                ],
            }
    except Exception:
        pass

    # Fallback: role-scoped budgets
    now = datetime.now(timezone.utc).isoformat()
    budgets = await db.execute_fetchall(
        "SELECT scope_id AS role, budget_usd, spent_usd, period "
        "FROM cost_budgets "
        "WHERE scope = 'role' AND (reset_at IS NULL OR reset_at > ?) "
        "ORDER BY spent_usd DESC",
        (now,),
    )
    return {
        "source": "budget_snapshot",
        "roles": [
            {
                "role": b["role"],
                "total_cost_usd": round(b["spent_usd"] or 0, 6),
                "budget_usd": b["budget_usd"] or 0,
                "period": b["period"],
            }
            for b in budgets
        ],
    }


# ------------------------------------------------------------------
# GET /api/costs/by-group — Cost breakdown by group
# ------------------------------------------------------------------


@router.get("/api/costs/by-group")
async def get_costs_by_group():
    """Return cost breakdown grouped by group/feature.

    Uses cost_attributions (feature_tag) if available,
    otherwise falls back to group-scoped budgets.
    """
    db = _get_db()

    # Try cost_attributions first (only use if it has data)
    try:
        rows = await db.execute_fetchall(
            "SELECT feature_tag AS group_id, "
            "SUM(cost_usd) AS total_cost, "
            "SUM(input_tokens) AS total_input_tokens, "
            "SUM(output_tokens) AS total_output_tokens, "
            "COUNT(*) AS record_count "
            "FROM cost_attributions "
            "WHERE feature_tag IS NOT NULL "
            "GROUP BY feature_tag "
            "ORDER BY SUM(cost_usd) DESC",
        )
        if rows:
            return {
                "source": "cost_attributions",
                "groups": [
                    {
                        "group_id": r["group_id"],
                        "total_cost_usd": round(r["total_cost"] or 0, 6),
                        "input_tokens": r["total_input_tokens"] or 0,
                        "output_tokens": r["total_output_tokens"] or 0,
                        "records": r["record_count"] or 0,
                    }
                    for r in rows
                ],
            }
    except Exception:
        pass

    # Fallback: group-scoped budgets
    now = datetime.now(timezone.utc).isoformat()
    budgets = await db.execute_fetchall(
        "SELECT scope_id AS group_id, budget_usd, spent_usd, period "
        "FROM cost_budgets "
        "WHERE scope = 'group' AND (reset_at IS NULL OR reset_at > ?) "
        "ORDER BY spent_usd DESC",
        (now,),
    )
    return {
        "source": "budget_snapshot",
        "groups": [
            {
                "group_id": b["group_id"],
                "total_cost_usd": round(b["spent_usd"] or 0, 6),
                "budget_usd": b["budget_usd"] or 0,
                "period": b["period"],
            }
            for b in budgets
        ],
    }
