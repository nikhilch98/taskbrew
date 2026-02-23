"""Export and reporting routes: CSV/JSON exports, summary reports, and analytics."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query
from starlette.responses import Response

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _csv_response(rows: list[dict], filename: str) -> Response:
    """Build a CSV download response from a list of dicts."""
    if not rows:
        return Response(
            content="",
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _json_response(data: dict, filename: str) -> Response:
    """Build a JSON download response."""
    return Response(
        content=json.dumps(data, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ------------------------------------------------------------------
# Full Export (enhanced version of the basic /api/export)
# ------------------------------------------------------------------


@router.get("/api/export/full")
async def export_full(
    fmt: str = Query("json", alias="format", description="json or csv"),
):
    """Export all tasks, groups, usage, and artifacts."""
    orch = get_orch()
    db = orch.task_board._db

    tasks = await db.execute_fetchall("SELECT * FROM tasks ORDER BY created_at")
    groups = await db.execute_fetchall("SELECT * FROM groups ORDER BY created_at")
    usage = await db.execute_fetchall("SELECT * FROM task_usage ORDER BY recorded_at")
    artifacts = await db.execute_fetchall("SELECT * FROM artifacts ORDER BY created_at")

    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "groups": groups,
        "tasks": tasks,
        "usage": usage,
        "artifacts": artifacts,
        "summary": {
            "total_groups": len(groups),
            "total_tasks": len(tasks),
            "total_usage_records": len(usage),
            "total_artifacts": len(artifacts),
        },
    }

    if fmt == "csv":
        return _csv_response(tasks, "full-export-tasks.csv")

    return _json_response(data, "full-export.json")


# ------------------------------------------------------------------
# Tasks Export with filters
# ------------------------------------------------------------------


@router.get("/api/export/tasks")
async def export_tasks(
    fmt: str = Query("json", alias="format"),
    status: str | None = None,
    group_id: str | None = None,
    assigned_to: str | None = None,
    priority: str | None = None,
    since: str | None = Query(None, description="ISO date filter, e.g. 2026-01-01"),
):
    """Export tasks with optional filters."""
    orch = get_orch()
    db = orch.task_board._db

    clauses: list[str] = []
    params: list = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if group_id:
        clauses.append("group_id = ?")
        params.append(group_id)
    if assigned_to:
        clauses.append("assigned_to = ?")
        params.append(assigned_to)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    tasks = await db.execute_fetchall(
        f"SELECT * FROM tasks{where} ORDER BY created_at",
        tuple(params),
    )

    if fmt == "csv":
        return _csv_response(tasks, "tasks-export.csv")

    return _json_response(
        {"exported_at": datetime.now(timezone.utc).isoformat(), "count": len(tasks), "tasks": tasks},
        "tasks-export.json",
    )


# ------------------------------------------------------------------
# Usage / Cost Export
# ------------------------------------------------------------------


@router.get("/api/export/usage")
async def export_usage(
    fmt: str = Query("json", alias="format"),
    days: int = Query(30, ge=1, le=365),
):
    """Export usage/cost data for the last N days."""
    orch = get_orch()
    db = orch.task_board._db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    usage = await db.execute_fetchall(
        "SELECT * FROM task_usage WHERE recorded_at >= ? ORDER BY recorded_at",
        (cutoff,),
    )

    if fmt == "csv":
        return _csv_response(usage, "usage-export.csv")

    return _json_response(
        {"exported_at": datetime.now(timezone.utc).isoformat(), "days": days, "count": len(usage), "records": usage},
        "usage-export.json",
    )


# ------------------------------------------------------------------
# Summary Report
# ------------------------------------------------------------------


@router.get("/api/reports/summary")
async def summary_report():
    """Generate an overall project summary report."""
    orch = get_orch()
    db = orch.task_board._db

    # Task counts by status
    status_rows = await db.execute_fetchall(
        "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
    )
    status_counts = {r["status"]: r["count"] for r in status_rows}

    # Task counts by priority
    priority_rows = await db.execute_fetchall(
        "SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority"
    )
    priority_counts = {r["priority"]: r["count"] for r in priority_rows}

    # Task counts by assigned_to
    assignee_rows = await db.execute_fetchall(
        "SELECT assigned_to, COUNT(*) as count FROM tasks GROUP BY assigned_to ORDER BY count DESC"
    )

    # Group summary
    group_rows = await db.execute_fetchall(
        "SELECT status, COUNT(*) as count FROM groups GROUP BY status"
    )
    group_counts = {r["status"]: r["count"] for r in group_rows}

    # Usage totals
    usage_row = await db.execute_fetchone(
        "SELECT COUNT(*) as total_runs, "
        "COALESCE(SUM(input_tokens), 0) as total_input_tokens, "
        "COALESCE(SUM(output_tokens), 0) as total_output_tokens, "
        "COALESCE(SUM(cost_usd), 0) as total_cost_usd, "
        "COALESCE(SUM(duration_api_ms), 0) as total_duration_ms "
        "FROM task_usage"
    )

    # Recent completions (last 7 days)
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_completions = await db.execute_fetchone(
        "SELECT COUNT(*) as count FROM tasks WHERE status = 'completed' AND completed_at >= ?",
        (week_ago,),
    )

    # Agent activity
    agent_rows = await db.execute_fetchall(
        "SELECT role, status, COUNT(*) as count FROM agent_instances GROUP BY role, status"
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tasks": {
            "by_status": status_counts,
            "by_priority": priority_counts,
            "by_assignee": [{"assignee": r["assigned_to"], "count": r["count"]} for r in assignee_rows],
            "total": sum(status_counts.values()),
            "completion_rate": round(
                status_counts.get("completed", 0) / max(sum(status_counts.values()), 1) * 100, 1
            ),
            "recent_completions_7d": recent_completions["count"] if recent_completions else 0,
        },
        "groups": group_counts,
        "usage": dict(usage_row) if usage_row else {},
        "agents": [dict(r) for r in agent_rows],
    }


# ------------------------------------------------------------------
# Velocity Report (tasks completed per day)
# ------------------------------------------------------------------


@router.get("/api/reports/velocity")
async def velocity_report(days: int = Query(30, ge=1, le=365)):
    """Daily task completion velocity for the past N days."""
    orch = get_orch()
    db = orch.task_board._db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.execute_fetchall(
        "SELECT DATE(completed_at) as day, COUNT(*) as completed "
        "FROM tasks WHERE status = 'completed' AND completed_at >= ? "
        "GROUP BY DATE(completed_at) ORDER BY day",
        (cutoff,),
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "velocity": [dict(r) for r in rows],
        "average_per_day": round(sum(r["completed"] for r in rows) / max(days, 1), 2),
    }


# ------------------------------------------------------------------
# Cost Report (spending by role/group/day)
# ------------------------------------------------------------------


@router.get("/api/reports/cost")
async def cost_report(days: int = Query(30, ge=1, le=365)):
    """Cost breakdown report by agent and day."""
    orch = get_orch()
    db = orch.task_board._db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Daily cost
    daily = await db.execute_fetchall(
        "SELECT DATE(recorded_at) as day, "
        "SUM(cost_usd) as cost, SUM(input_tokens) as input_tokens, "
        "SUM(output_tokens) as output_tokens, COUNT(*) as runs "
        "FROM task_usage WHERE recorded_at >= ? "
        "GROUP BY DATE(recorded_at) ORDER BY day",
        (cutoff,),
    )

    # By agent
    by_agent = await db.execute_fetchall(
        "SELECT agent_id, SUM(cost_usd) as cost, COUNT(*) as runs, "
        "SUM(input_tokens) as input_tokens, SUM(output_tokens) as output_tokens "
        "FROM task_usage WHERE recorded_at >= ? "
        "GROUP BY agent_id ORDER BY cost DESC",
        (cutoff,),
    )

    # By model
    by_model = await db.execute_fetchall(
        "SELECT model, SUM(cost_usd) as cost, COUNT(*) as runs "
        "FROM task_usage WHERE recorded_at >= ? "
        "GROUP BY model ORDER BY cost DESC",
        (cutoff,),
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "daily": [dict(r) for r in daily],
        "by_agent": [dict(r) for r in by_agent],
        "by_model": [dict(r) for r in by_model],
        "total_cost": sum(r["cost"] or 0 for r in daily),
    }
