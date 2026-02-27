"""Agent performance analytics: per-agent stats, throughput, efficiency metrics."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


# ------------------------------------------------------------------
# Agent Performance Summary
# ------------------------------------------------------------------


@router.get("/api/analytics/agents")
async def agent_performance_summary(days: int = Query(30, ge=1, le=365)):
    """Per-agent performance metrics: tasks completed, avg duration, cost, success rate."""
    orch = get_orch()
    db = orch.task_board._db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    rows = await db.execute_fetchall(
        "SELECT "
        "  u.agent_id, "
        "  COUNT(*) as total_runs, "
        "  COALESCE(SUM(u.input_tokens), 0) as total_input_tokens, "
        "  COALESCE(SUM(u.output_tokens), 0) as total_output_tokens, "
        "  COALESCE(SUM(u.cost_usd), 0) as total_cost, "
        "  COALESCE(AVG(u.cost_usd), 0) as avg_cost_per_run, "
        "  COALESCE(AVG(u.duration_api_ms), 0) as avg_duration_ms, "
        "  COALESCE(SUM(u.num_turns), 0) as total_turns, "
        "  COALESCE(AVG(u.num_turns), 0) as avg_turns_per_run "
        "FROM task_usage u "
        "WHERE u.recorded_at >= ? "
        "GROUP BY u.agent_id "
        "ORDER BY total_runs DESC",
        (cutoff,),
    )

    agents = []
    for r in rows:
        # Get task completion stats for this agent
        task_stats = await db.execute_fetchone(
            "SELECT "
            "  COUNT(*) as total_tasks, "
            "  SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed, "
            "  SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed "
            "FROM tasks WHERE claimed_by = ? AND created_at >= ?",
            (r["agent_id"], cutoff),
        )

        total_tasks = task_stats["total_tasks"] if task_stats else 0
        completed = task_stats["completed"] if task_stats else 0
        failed = task_stats["failed"] if task_stats else 0
        success_rate = round(completed / max(total_tasks, 1) * 100, 1)

        agents.append({
            "agent_id": r["agent_id"],
            "total_runs": r["total_runs"],
            "total_input_tokens": r["total_input_tokens"],
            "total_output_tokens": r["total_output_tokens"],
            "total_cost": round(r["total_cost"], 4),
            "avg_cost_per_run": round(r["avg_cost_per_run"], 4),
            "avg_duration_ms": round(r["avg_duration_ms"]),
            "total_turns": r["total_turns"],
            "avg_turns_per_run": round(r["avg_turns_per_run"], 1),
            "tasks_completed": completed,
            "tasks_failed": failed,
            "success_rate": success_rate,
        })

    return {"days": days, "agents": agents}


# ------------------------------------------------------------------
# Agent Detail
# ------------------------------------------------------------------


@router.get("/api/analytics/agents/{agent_id}")
async def agent_detail(agent_id: str, days: int = Query(30, ge=1, le=365)):
    """Detailed performance data for a single agent."""
    orch = get_orch()
    db = orch.task_board._db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Usage over time
    daily = await db.execute_fetchall(
        "SELECT DATE(recorded_at) as day, "
        "  COUNT(*) as runs, "
        "  SUM(cost_usd) as cost, "
        "  AVG(duration_api_ms) as avg_duration_ms, "
        "  SUM(input_tokens) as input_tokens, "
        "  SUM(output_tokens) as output_tokens "
        "FROM task_usage "
        "WHERE agent_id = ? AND recorded_at >= ? "
        "GROUP BY DATE(recorded_at) ORDER BY day",
        (agent_id, cutoff),
    )

    # Recent tasks
    recent_tasks = await db.execute_fetchall(
        "SELECT id, title, status, priority, started_at, completed_at "
        "FROM tasks WHERE claimed_by = ? AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 20",
        (agent_id, cutoff),
    )

    # Instance info
    instance = await db.execute_fetchone(
        "SELECT * FROM agent_instances WHERE instance_id = ?",
        (agent_id,),
    )

    return {
        "agent_id": agent_id,
        "days": days,
        "daily_usage": [dict(r) for r in daily],
        "recent_tasks": [dict(r) for r in recent_tasks],
        "instance": dict(instance) if instance else None,
    }


# ------------------------------------------------------------------
# Throughput metrics (tasks per hour/day)
# ------------------------------------------------------------------


@router.get("/api/analytics/throughput")
async def throughput_metrics(days: int = Query(7, ge=1, le=90)):
    """Hourly and daily task throughput."""
    orch = get_orch()
    db = orch.task_board._db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Daily throughput
    daily = await db.execute_fetchall(
        "SELECT DATE(completed_at) as day, COUNT(*) as completed "
        "FROM tasks WHERE status = 'completed' AND completed_at >= ? "
        "GROUP BY DATE(completed_at) ORDER BY day",
        (cutoff,),
    )

    # By role
    by_role = await db.execute_fetchall(
        "SELECT assigned_to as role, COUNT(*) as completed "
        "FROM tasks WHERE status = 'completed' AND completed_at >= ? "
        "GROUP BY assigned_to ORDER BY completed DESC",
        (cutoff,),
    )

    total_completed = sum(r["completed"] for r in daily)

    return {
        "days": days,
        "total_completed": total_completed,
        "avg_per_day": round(total_completed / max(days, 1), 2),
        "daily": [dict(r) for r in daily],
        "by_role": [dict(r) for r in by_role],
    }


# ------------------------------------------------------------------
# Efficiency metrics (cost per task, tokens per task)
# ------------------------------------------------------------------


@router.get("/api/analytics/efficiency")
async def efficiency_metrics(days: int = Query(30, ge=1, le=365)):
    """Cost and token efficiency metrics."""
    orch = get_orch()
    db = orch.task_board._db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Overall efficiency
    overall = await db.execute_fetchone(
        "SELECT "
        "  COUNT(*) as total_runs, "
        "  COALESCE(AVG(cost_usd), 0) as avg_cost, "
        "  COALESCE(AVG(input_tokens + output_tokens), 0) as avg_tokens, "
        "  COALESCE(AVG(duration_api_ms), 0) as avg_duration_ms, "
        "  COALESCE(AVG(num_turns), 0) as avg_turns "
        "FROM task_usage WHERE recorded_at >= ?",
        (cutoff,),
    )

    # By priority
    by_priority = await db.execute_fetchall(
        "SELECT t.priority, "
        "  COUNT(*) as runs, "
        "  AVG(u.cost_usd) as avg_cost, "
        "  AVG(u.duration_api_ms) as avg_duration_ms "
        "FROM task_usage u "
        "JOIN tasks t ON u.task_id = t.id "
        "WHERE u.recorded_at >= ? "
        "GROUP BY t.priority",
        (cutoff,),
    )

    # By task type
    by_type = await db.execute_fetchall(
        "SELECT t.task_type, "
        "  COUNT(*) as runs, "
        "  AVG(u.cost_usd) as avg_cost, "
        "  AVG(u.duration_api_ms) as avg_duration_ms "
        "FROM task_usage u "
        "JOIN tasks t ON u.task_id = t.id "
        "WHERE u.recorded_at >= ? "
        "GROUP BY t.task_type",
        (cutoff,),
    )

    return {
        "days": days,
        "overall": dict(overall) if overall else {},
        "by_priority": [dict(r) for r in by_priority],
        "by_task_type": [dict(r) for r in by_type],
    }
