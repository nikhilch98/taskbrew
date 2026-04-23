"""Export and reporting routes: CSV/JSON exports, summary reports, and analytics."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query
from starlette.responses import Response

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


# audit 11a F#5: CSV formula-injection guard.
#
# Excel, Google Sheets, LibreOffice, Numbers and friends all treat a cell
# beginning with any of these prefixes as a formula and evaluate it on
# open. A task title like ``=HYPERLINK("http://evil",A1)`` from a
# compromised agent would trigger when an operator opens the CSV export.
# The canonical mitigation (OWASP "CSV Injection") is to prefix a single
# quote -- consuming tools strip it on display but formula parsers treat
# the cell as plain text.
_CSV_UNSAFE_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _escape_csv_cell(value):
    """Return *value* in a form safe to write to a CSV cell.

    Strings starting with an unsafe prefix are escaped with a leading
    single-quote. Non-string values pass through unchanged; CSV writers
    stringify them without interpreting prefixes.
    """
    if isinstance(value, str) and value.startswith(_CSV_UNSAFE_PREFIXES):
        return "'" + value
    return value


def _escape_row(row: dict) -> dict:
    return {k: _escape_csv_cell(v) for k, v in row.items()}


# audit 11a F#7: /api/export/* used to pull the full table into
# memory. For realistic deployments that table can be hundreds of
# thousands of rows. We cap the row count at a hard maximum so a
# single request cannot OOM the process. Callers that need more
# should page through /api/tasks or run export/usage in day-bounded
# slices.
MAX_EXPORT_ROWS = 50_000


def _capped_query(base_sql: str) -> str:
    """Return ``base_sql`` with a LIMIT MAX_EXPORT_ROWS+1 clause so we
    can detect when the cap has been hit and flag it."""
    return f"{base_sql} LIMIT {MAX_EXPORT_ROWS + 1}"


def _truncate_and_flag(rows: list[dict]) -> tuple[list[dict], bool]:
    if len(rows) > MAX_EXPORT_ROWS:
        return rows[:MAX_EXPORT_ROWS], True
    return rows, False


# audit 11a F#6: per-endpoint column allowlists for CSV exports.
# Previously ``_csv_response`` took ``rows[0].keys()`` as the CSV
# schema, which meant adding any new internal column to the ``tasks``
# / ``task_usage`` / ``artifacts`` / ``groups`` tables silently
# widened the exported CSV -- a latent data-leak path for fields we
# never intended to hand out. The export surface is a public API;
# its column shape must be declared, not inferred.
_CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "tasks": (
        "id", "title", "description", "status", "priority", "task_type",
        "group_id", "assigned_to", "created_at", "started_at",
        "completed_at", "claim_count", "failure_count",
    ),
    "task_usage": (
        "id", "task_id", "agent_id", "model", "cost_usd",
        "input_tokens", "output_tokens", "num_turns",
        "duration_api_ms", "recorded_at",
    ),
    "artifacts": (
        "id", "task_id", "agent_id", "kind", "name", "size_bytes",
        "created_at",
    ),
    "groups": (
        "id", "title", "description", "status", "progress",
        "created_at", "updated_at",
    ),
}


def _csv_response(
    rows: list[dict],
    filename: str,
    *,
    columns: tuple[str, ...] | None = None,
) -> Response:
    """Build a CSV download response from a list of dicts.

    All string cells beginning with ``= + - @ \\t \\r`` are escaped via
    :func:`_escape_csv_cell` before writing, which prevents spreadsheet
    formula injection from LLM-authored task titles / descriptions /
    error messages landing in downloaded exports.

    ``columns`` -- if provided, the CSV is projected to exactly these
    fields in this order, regardless of which columns the source rows
    carry. This is the safe default for endpoint code; callers that
    pass ``None`` fall back to ``rows[0].keys()`` for backward
    compatibility, but should be migrated off.
    """
    if not rows:
        return Response(
            content="",
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    if columns is None:
        columns = tuple(rows[0].keys())
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    projected = ({k: r.get(k) for k in columns} for r in rows)
    writer.writerows(_escape_row(r) for r in projected)
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

    tasks_rows = await db.execute_fetchall(_capped_query("SELECT * FROM tasks ORDER BY created_at"))
    groups_rows = await db.execute_fetchall(_capped_query("SELECT * FROM groups ORDER BY created_at"))
    usage_rows = await db.execute_fetchall(_capped_query("SELECT * FROM task_usage ORDER BY recorded_at"))
    artifacts_rows = await db.execute_fetchall(_capped_query("SELECT * FROM artifacts ORDER BY created_at"))
    tasks, tasks_truncated = _truncate_and_flag(tasks_rows)
    groups, groups_truncated = _truncate_and_flag(groups_rows)
    usage, usage_truncated = _truncate_and_flag(usage_rows)
    artifacts, artifacts_truncated = _truncate_and_flag(artifacts_rows)

    data = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "max_rows_per_table": MAX_EXPORT_ROWS,
        "truncated": {
            "tasks": tasks_truncated,
            "groups": groups_truncated,
            "usage": usage_truncated,
            "artifacts": artifacts_truncated,
        },
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
        return _csv_response(tasks, "full-export-tasks.csv", columns=_CSV_COLUMNS["tasks"])

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
        # audit 11a F#19: reject malformed timestamps BEFORE they reach
        # SQLite; a stray free-text value could silently match every
        # row or produce a cast error surfaced to the client as 500.
        try:
            datetime.fromisoformat(since.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid 'since' value {since!r}; expected ISO-8601 "
                       "date or datetime (e.g. 2026-01-01 or 2026-01-01T00:00:00Z)",
            )
        clauses.append("created_at >= ?")
        params.append(since)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = await db.execute_fetchall(
        _capped_query(f"SELECT * FROM tasks{where} ORDER BY created_at"),
        tuple(params),
    )
    tasks, truncated = _truncate_and_flag(rows)

    if fmt == "csv":
        return _csv_response(tasks, "tasks-export.csv", columns=_CSV_COLUMNS["tasks"])

    return _json_response(
        {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(tasks),
            "truncated": truncated,
            "max_rows": MAX_EXPORT_ROWS,
            "tasks": tasks,
        },
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
    rows = await db.execute_fetchall(
        _capped_query("SELECT * FROM task_usage WHERE recorded_at >= ? ORDER BY recorded_at"),
        (cutoff,),
    )
    usage, truncated = _truncate_and_flag(rows)

    if fmt == "csv":
        return _csv_response(usage, "usage-export.csv", columns=_CSV_COLUMNS["task_usage"])

    return _json_response(
        {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "days": days,
            "count": len(usage),
            "truncated": truncated,
            "max_rows": MAX_EXPORT_ROWS,
            "records": usage,
        },
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
