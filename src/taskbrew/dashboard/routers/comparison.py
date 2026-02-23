"""Cross-project comparison: summary stats and metric comparison across projects."""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
import yaml
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()

# ------------------------------------------------------------------
# Dependency injection -- set by app.py
# ------------------------------------------------------------------
_project_manager = None


def set_comparison_deps(project_manager):
    """Called by app.py to inject the project manager reference."""
    global _project_manager
    _project_manager = project_manager


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _resolve_db_path(project: dict) -> str | None:
    """Read a project's config/team.yaml and return the absolute db path, or None."""
    project_dir = Path(project["directory"])
    team_yaml = project_dir / "config" / "team.yaml"
    if not team_yaml.exists():
        return None
    try:
        with open(team_yaml) as f:
            data = yaml.safe_load(f)
        db_path = data.get("database", {}).get("path", "data/taskbrew.db")
        resolved = project_dir / db_path
        return str(resolved)
    except Exception:
        logger.warning("Failed to read team.yaml for project %s", project.get("id"))
        return None


async def _query_project_stats(db_path: str) -> dict:
    """Open a project's database and gather summary statistics.

    Returns a dict with task counts, completion rate, cost, agent count,
    and group count.  If the database cannot be opened or a table is missing,
    returns safe defaults.
    """
    defaults = {
        "total_tasks": 0,
        "completed_tasks": 0,
        "completion_rate": 0.0,
        "total_cost": 0.0,
        "active_agents": 0,
        "groups_count": 0,
    }

    db_file = Path(db_path)
    if not db_file.exists():
        return defaults

    try:
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row

            # Task counts
            cursor = await conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed "
                "FROM tasks"
            )
            row = await cursor.fetchone()
            total_tasks = row[0] or 0
            completed_tasks = row[1] or 0
            completion_rate = round(
                completed_tasks / total_tasks * 100, 1
            ) if total_tasks > 0 else 0.0

            # Total cost from task_usage
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total_cost FROM task_usage"
            )
            row = await cursor.fetchone()
            total_cost = round(row[0] or 0.0, 4)

            # Active agents (status = 'busy' or has recent heartbeat)
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT instance_id) as cnt "
                "FROM agent_instances WHERE status != 'stopped'"
            )
            row = await cursor.fetchone()
            active_agents = row[0] or 0

            # Groups count
            cursor = await conn.execute("SELECT COUNT(*) as cnt FROM groups")
            row = await cursor.fetchone()
            groups_count = row[0] or 0

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "completion_rate": completion_rate,
            "total_cost": total_cost,
            "active_agents": active_agents,
            "groups_count": groups_count,
        }
    except Exception as exc:
        logger.warning("Failed to query stats for %s: %s", db_path, exc)
        return defaults


async def _query_project_metrics(db_path: str) -> dict:
    """Query detailed metrics for cross-project comparison.

    Returns velocity (tasks completed per day over last 7 days),
    cost breakdown, and task status distribution.
    """
    defaults = {
        "tasks_completed": 0,
        "tasks_in_progress": 0,
        "tasks_pending": 0,
        "tasks_failed": 0,
        "total_cost": 0.0,
        "velocity_7d": 0.0,
        "avg_cost_per_task": 0.0,
    }

    db_file = Path(db_path)
    if not db_file.exists():
        return defaults

    try:
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row

            # Task status distribution
            cursor = await conn.execute(
                "SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status"
            )
            rows = await cursor.fetchall()
            status_map = {row[0]: row[1] for row in rows}

            tasks_completed = status_map.get("completed", 0)
            tasks_in_progress = status_map.get("in_progress", 0)
            tasks_pending = status_map.get("pending", 0) + status_map.get("blocked", 0)
            tasks_failed = status_map.get("failed", 0)

            # Total cost
            cursor = await conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) as total_cost FROM task_usage"
            )
            row = await cursor.fetchone()
            total_cost = round(row[0] or 0.0, 4)

            # Average cost per completed task
            avg_cost = round(total_cost / tasks_completed, 4) if tasks_completed > 0 else 0.0

            # Velocity: tasks completed in last 7 days / 7
            cursor = await conn.execute(
                "SELECT COUNT(*) as cnt FROM tasks "
                "WHERE status = 'completed' "
                "AND completed_at >= datetime('now', '-7 days')"
            )
            row = await cursor.fetchone()
            recent_completed = row[0] or 0
            velocity_7d = round(recent_completed / 7, 2)

        return {
            "tasks_completed": tasks_completed,
            "tasks_in_progress": tasks_in_progress,
            "tasks_pending": tasks_pending,
            "tasks_failed": tasks_failed,
            "total_cost": total_cost,
            "velocity_7d": velocity_7d,
            "avg_cost_per_task": avg_cost,
        }
    except Exception as exc:
        logger.warning("Failed to query metrics for %s: %s", db_path, exc)
        return defaults


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/api/comparison/projects")
async def comparison_projects():
    """List all projects with summary stats (task counts, completion rates)."""
    if not _project_manager:
        return []

    projects = _project_manager.list_projects()
    results = []

    for project in projects:
        db_path = _resolve_db_path(project)
        if db_path:
            stats = await _query_project_stats(db_path)
        else:
            stats = {
                "total_tasks": 0,
                "completed_tasks": 0,
                "completion_rate": 0.0,
                "total_cost": 0.0,
                "active_agents": 0,
                "groups_count": 0,
            }

        results.append({
            "id": project["id"],
            "name": project["name"],
            "directory": project["directory"],
            "created_at": project.get("created_at"),
            **stats,
        })

    return results


@router.get("/api/comparison/metrics")
async def comparison_metrics(
    project_ids: str | None = Query(
        None, description="Comma-separated project IDs to compare. Omit for all."
    ),
):
    """Compare key metrics across projects (tasks completed, cost, velocity)."""
    if not _project_manager:
        return {"projects": []}

    projects = _project_manager.list_projects()

    # Filter by requested IDs if provided
    if project_ids:
        requested = {pid.strip() for pid in project_ids.split(",")}
        projects = [p for p in projects if p["id"] in requested]
        # Validate that all requested IDs were found
        found_ids = {p["id"] for p in projects}
        missing = requested - found_ids
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Projects not found: {', '.join(sorted(missing))}",
            )

    results = []
    for project in projects:
        db_path = _resolve_db_path(project)
        if db_path:
            metrics = await _query_project_metrics(db_path)
        else:
            metrics = {
                "tasks_completed": 0,
                "tasks_in_progress": 0,
                "tasks_pending": 0,
                "tasks_failed": 0,
                "total_cost": 0.0,
                "velocity_7d": 0.0,
                "avg_cost_per_task": 0.0,
            }

        results.append({
            "id": project["id"],
            "name": project["name"],
            **metrics,
        })

    return {"projects": results}
