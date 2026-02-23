"""Pipeline execution visualization: workflow runs, step tracking, execution history."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Query

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


# ------------------------------------------------------------------
# Workflow execution overview
# ------------------------------------------------------------------


@router.get("/api/pipelines/workflows")
async def list_workflows():
    """List all workflow definitions with execution stats."""
    orch = get_orch()
    db = orch.task_board._db

    workflows = await db.execute_fetchall(
        "SELECT * FROM workflow_definitions ORDER BY created_at DESC"
    )

    result = []
    for wf in workflows:
        steps = json.loads(wf["steps"]) if wf["steps"] else []
        result.append({
            "id": wf["id"],
            "name": wf["name"],
            "description": wf["description"],
            "active": bool(wf["active"]),
            "steps_count": len(steps),
            "steps": steps,
            "created_at": wf["created_at"],
        })

    return {"workflows": result, "count": len(result)}


# ------------------------------------------------------------------
# Group-as-pipeline view
# ------------------------------------------------------------------


@router.get("/api/pipelines/groups")
async def list_pipeline_groups(status: str | None = None):
    """List groups as pipeline runs, with task progress for each."""
    orch = get_orch()
    db = orch.task_board._db

    if status:
        groups = await db.execute_fetchall(
            "SELECT * FROM groups WHERE status = ? ORDER BY created_at DESC",
            (status,),
        )
    else:
        groups = await db.execute_fetchall(
            "SELECT * FROM groups ORDER BY created_at DESC"
        )

    result = []
    for g in groups:
        task_stats = await db.execute_fetchone(
            "SELECT "
            "  COUNT(*) as total, "
            "  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed, "
            "  SUM(CASE WHEN status = 'in_progress' THEN 1 ELSE 0 END) as in_progress, "
            "  SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending, "
            "  SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) as blocked, "
            "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed "
            "FROM tasks WHERE group_id = ?",
            (g["id"],),
        )
        total = task_stats["total"] if task_stats else 0
        completed = task_stats["completed"] if task_stats else 0
        progress = round(completed / max(total, 1) * 100, 1)

        result.append({
            "id": g["id"],
            "title": g["title"],
            "origin": g["origin"],
            "status": g["status"],
            "created_by": g["created_by"],
            "created_at": g["created_at"],
            "completed_at": g["completed_at"],
            "tasks": {
                "total": total,
                "completed": completed,
                "in_progress": task_stats["in_progress"] if task_stats else 0,
                "pending": task_stats["pending"] if task_stats else 0,
                "blocked": task_stats["blocked"] if task_stats else 0,
                "failed": task_stats["failed"] if task_stats else 0,
            },
            "progress": progress,
        })

    return {"groups": result, "count": len(result)}


# ------------------------------------------------------------------
# Pipeline detail (group with tasks as steps)
# ------------------------------------------------------------------


@router.get("/api/pipelines/groups/{group_id}")
async def pipeline_detail(group_id: str):
    """Get detailed pipeline view for a group, including task dependency graph."""
    orch = get_orch()
    db = orch.task_board._db

    group = await db.execute_fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))
    if not group:
        raise HTTPException(404, f"Group not found: {group_id}")

    tasks = await db.execute_fetchall(
        "SELECT * FROM tasks WHERE group_id = ? ORDER BY created_at",
        (group_id,),
    )

    # Get dependencies
    deps = await db.execute_fetchall(
        "SELECT td.task_id, td.blocked_by, td.resolved "
        "FROM task_dependencies td "
        "JOIN tasks t ON td.task_id = t.id "
        "WHERE t.group_id = ?",
        (group_id,),
    )

    # Build graph edges
    edges = [{"from": d["blocked_by"], "to": d["task_id"], "resolved": bool(d["resolved"])} for d in deps]

    # Task nodes with usage data
    nodes = []
    for t in tasks:
        usage = await db.execute_fetchone(
            "SELECT SUM(cost_usd) as cost, SUM(duration_api_ms) as duration, COUNT(*) as runs "
            "FROM task_usage WHERE task_id = ?",
            (t["id"],),
        )
        nodes.append({
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "priority": t["priority"],
            "assigned_to": t["assigned_to"],
            "claimed_by": t["claimed_by"],
            "created_at": t["created_at"],
            "completed_at": t["completed_at"],
            "cost": round(usage["cost"] or 0, 4) if usage else 0,
            "duration_ms": (usage["duration"] or 0) if usage else 0,
            "runs": (usage["runs"] or 0) if usage else 0,
        })

    return {
        "group": dict(group),
        "nodes": nodes,
        "edges": edges,
        "total_tasks": len(nodes),
    }


# ------------------------------------------------------------------
# Pipeline execution history
# ------------------------------------------------------------------


@router.get("/api/pipelines/history")
async def pipeline_history(days: int = Query(30, ge=1, le=365)):
    """Group completion history — how many groups completed per day."""
    orch = get_orch()
    db = orch.task_board._db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    daily = await db.execute_fetchall(
        "SELECT DATE(completed_at) as day, COUNT(*) as completed "
        "FROM groups WHERE status = 'completed' AND completed_at >= ? "
        "GROUP BY DATE(completed_at) ORDER BY day",
        (cutoff,),
    )

    return {
        "days": days,
        "history": [dict(r) for r in daily],
        "total_completed": sum(r["completed"] for r in daily),
    }
