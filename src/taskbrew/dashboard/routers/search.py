"""Global search across all entities: tasks, groups, agents, artifacts, events."""

from __future__ import annotations

from fastapi import APIRouter, Query

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


@router.get("/api/search")
async def global_search(
    q: str = Query(..., min_length=1, description="Search query"),
    entity: str | None = Query(None, description="Filter by entity type: tasks, groups, agents, artifacts, events"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search across all entities. Returns results grouped by type."""
    orch = get_orch()
    db = orch.task_board._db

    like = f"%{q}%"
    results: dict[str, list] = {}

    if entity is None or entity == "tasks":
        tasks = await db.execute_fetchall(
            "SELECT id, group_id, title, description, status, priority, assigned_to, claimed_by, created_at "
            "FROM tasks WHERE title LIKE ? OR description LIKE ? OR id LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["tasks"] = [dict(r) for r in tasks]

    if entity is None or entity == "groups":
        groups = await db.execute_fetchall(
            "SELECT id, title, origin, status, created_by, created_at "
            "FROM groups WHERE title LIKE ? OR id LIKE ? OR origin LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["groups"] = [dict(r) for r in groups]

    if entity is None or entity == "agents":
        agents = await db.execute_fetchall(
            "SELECT instance_id, role, status, current_task, started_at, last_heartbeat "
            "FROM agent_instances WHERE instance_id LIKE ? OR role LIKE ? "
            "ORDER BY started_at DESC LIMIT ?",
            (like, like, limit),
        )
        results["agents"] = [dict(r) for r in agents]

    if entity is None or entity == "artifacts":
        artifacts = await db.execute_fetchall(
            "SELECT id, task_id, file_path, artifact_type, created_at "
            "FROM artifacts WHERE file_path LIKE ? OR id LIKE ? OR artifact_type LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["artifacts"] = [dict(r) for r in artifacts]

    if entity is None or entity == "events":
        events = await db.execute_fetchall(
            "SELECT id, event_type, group_id, task_id, agent_id, data, created_at "
            "FROM events WHERE event_type LIKE ? OR data LIKE ? OR task_id LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["events"] = [dict(r) for r in events]

    total = sum(len(v) for v in results.values())
    return {"query": q, "total": total, "results": results}
