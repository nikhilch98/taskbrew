"""Global search across all entities: tasks, groups, agents, artifacts, events."""

from __future__ import annotations

from fastapi import APIRouter, Query

from taskbrew.dashboard.routers._deps import get_orch

router = APIRouter()


def _escape_like(query: str) -> str:
    """Escape SQLite LIKE wildcards in user input.

    audit 10 F#19: `%` matches any run of characters, `_` matches one.
    A q of ``"%"`` turned into LIKE ``"%%%"`` which is a full-table
    scan across five tables. We use backslash as the escape character
    and signal it to the engine via ``ESCAPE '\\'`` in the WHERE
    clause (see below).
    """
    return (
        query.replace("\\", "\\\\")
             .replace("%", "\\%")
             .replace("_", "\\_")
    )


@router.get("/api/search")
async def global_search(
    # max_length caps a pathological q that would otherwise scan huge
    # substrings; 256 chars is plenty for real search UX.
    q: str = Query(..., min_length=1, max_length=256, description="Search query"),
    entity: str | None = Query(None, description="Filter by entity type: tasks, groups, agents, artifacts, events"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search across all entities. Returns results grouped by type.

    audit 10 F#19: LIKE wildcards (``%`` and ``_``) are escaped and
    the patterns pass through SQLite's explicit ``ESCAPE '\\'`` clause
    so user input cannot turn every search into a full-table scan.
    """
    orch = get_orch()
    db = orch.task_board._db

    like = f"%{_escape_like(q)}%"
    results: dict[str, list] = {}

    if entity is None or entity == "tasks":
        tasks = await db.execute_fetchall(
            "SELECT id, group_id, title, description, status, priority, assigned_to, claimed_by, created_at "
            "FROM tasks WHERE title LIKE ? ESCAPE '\\' "
            "OR description LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["tasks"] = [dict(r) for r in tasks]

    if entity is None or entity == "groups":
        groups = await db.execute_fetchall(
            "SELECT id, title, origin, status, created_by, created_at "
            "FROM groups WHERE title LIKE ? ESCAPE '\\' "
            "OR id LIKE ? ESCAPE '\\' OR origin LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["groups"] = [dict(r) for r in groups]

    if entity is None or entity == "agents":
        agents = await db.execute_fetchall(
            "SELECT instance_id, role, status, current_task, started_at, last_heartbeat "
            "FROM agent_instances WHERE instance_id LIKE ? ESCAPE '\\' "
            "OR role LIKE ? ESCAPE '\\' "
            "ORDER BY started_at DESC LIMIT ?",
            (like, like, limit),
        )
        results["agents"] = [dict(r) for r in agents]

    if entity is None or entity == "artifacts":
        artifacts = await db.execute_fetchall(
            "SELECT id, task_id, file_path, artifact_type, created_at "
            "FROM artifacts WHERE file_path LIKE ? ESCAPE '\\' "
            "OR id LIKE ? ESCAPE '\\' OR artifact_type LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["artifacts"] = [dict(r) for r in artifacts]

    if entity is None or entity == "events":
        events = await db.execute_fetchall(
            "SELECT id, event_type, group_id, task_id, agent_id, data, created_at "
            "FROM events WHERE event_type LIKE ? ESCAPE '\\' "
            "OR data LIKE ? ESCAPE '\\' OR task_id LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
            (like, like, like, limit),
        )
        results["events"] = [dict(r) for r in events]

    total = sum(len(v) for v in results.values())
    return {"query": q, "total": total, "results": results}
