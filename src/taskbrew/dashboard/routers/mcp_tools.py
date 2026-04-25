"""MCP tool endpoints for agent-orchestrator communication.

Audit 10 F#2 note: _get_token previously only verified the ``Bearer ``
prefix and returned the suffix verbatim. Any caller could invoke any
/mcp/tools/* endpoint with a header like ``Authorization: Bearer x`` and
be treated as authenticated. _get_token now delegates suffix verification
to the shared :class:`AuthManager` when auth is enabled.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Header
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)

_interaction_mgr = None
_pipeline_getter = None
_task_board = None
_auth_manager = None
_event_bus = None
_orchestrator_getter = None
_auth_warning_emitted = False


def set_mcp_deps(
    interaction_mgr,
    pipeline_getter,
    task_board=None,
    auth_manager=None,
    event_bus=None,
    orchestrator_getter=None,
):
    """Set dependencies. Called by app.py at startup.

    *auth_manager* is optional for backward compatibility. When supplied
    (strongly recommended) _get_token validates the bearer token suffix
    against the configured AuthManager; without it, a log warning is
    emitted on first use and any non-empty suffix is accepted (legacy
    behavior).

    *event_bus* is used by record_check to emit task.check_recorded
    events; if omitted the tool still writes to the DB but the WS
    consumer won't see live updates.

    *orchestrator_getter* is a callable that returns the active
    orchestrator (or None). Used by complete_task to resolve the
    agent's worktree path and ingest declared artifact_paths into
    the artifact_store. If omitted the ingestion silently no-ops --
    the rest of complete_task still works.
    """
    global _interaction_mgr, _pipeline_getter, _task_board
    global _auth_manager, _event_bus, _orchestrator_getter
    _interaction_mgr = interaction_mgr
    _pipeline_getter = pipeline_getter
    _task_board = task_board
    _auth_manager = auth_manager
    _event_bus = event_bus
    _orchestrator_getter = orchestrator_getter


def _get_token(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization[7:]
    if not token:
        raise HTTPException(401, "Empty bearer token")

    global _auth_warning_emitted
    if _auth_manager is None:
        if not _auth_warning_emitted:
            logger.warning(
                "MCP _get_token has no AuthManager wired in — accepting any "
                "non-empty Bearer suffix. Call set_mcp_deps(..., "
                "auth_manager=<AuthManager>) to enable token verification."
            )
            _auth_warning_emitted = True
        return token

    # When auth is enabled, the suffix MUST match a known token hash.
    # When auth is disabled, verify_token_string() returns True (legacy).
    if not _auth_manager.verify_token_string(token):
        raise HTTPException(401, "Invalid bearer token")
    return token


_MAX_INGEST_ARTIFACT_COUNT = 50


async def _ingest_artifact_paths(
    *,
    task_id: str,
    group_id: str,
    artifact_paths,
) -> list[str]:
    """Copy each declared artifact_path from the agent's worktree into
    the artifact_store under ``<group>/<task>/<basename>`` so the
    dashboard's artifact viewer can find it later.

    Validates every path is contained inside the agent's own worktree
    (no ``../etc/passwd``). Skips silently when the prerequisites for
    ingestion aren't available -- the agent's complete_task call still
    succeeds even if we can't copy the files (e.g., test fixtures with
    no real worktree manager).

    Returns the list of basenames successfully ingested.
    """
    if not artifact_paths or not isinstance(artifact_paths, list):
        return []
    if not _task_board or not _orchestrator_getter:
        return []
    orch = _orchestrator_getter()
    worktree_mgr = getattr(orch, "worktree_manager", None) if orch else None
    if not worktree_mgr:
        return []

    # Look up the task's claimant so we know which agent's worktree
    # to read from.
    task = await _task_board.get_task(task_id)
    if not task:
        return []
    claimed_by = task.get("claimed_by") or ""
    worktree_path = worktree_mgr.get_worktree_path(claimed_by)
    if not worktree_path or not os.path.isdir(worktree_path):
        return []

    # Cap how many paths we'll process per call so a hostile / careless
    # agent can't trigger 10k file copies.
    paths_to_process = artifact_paths[:_MAX_INGEST_ARTIFACT_COUNT]

    from taskbrew.orchestrator.artifact_store import ArtifactStore
    base = orch.artifact_store.base_dir if hasattr(orch, "artifact_store") else None
    if not base:
        # Fall back to project_dir/artifacts to match the dashboard's
        # _artifact_base_dir resolution.
        tc = getattr(orch, "team_config", None)
        artifacts_subdir = getattr(tc, "artifacts_base_dir", "artifacts") if tc else "artifacts"
        base = os.path.join(orch.project_dir, artifacts_subdir)
    store = ArtifactStore(base_dir=str(base))

    worktree_real = os.path.realpath(worktree_path)
    ingested: list[str] = []
    for path_entry in paths_to_process:
        if not isinstance(path_entry, str) or not path_entry.strip():
            continue
        # Reject absolute and parent-traversal inputs at the API
        # boundary; rely on realpath containment as defense-in-depth.
        if path_entry.startswith("/") or ".." in path_entry.split("/"):
            logger.warning(
                "Rejecting artifact_path %r for task %s: not relative or contains '..'",
                path_entry, task_id,
            )
            continue
        full = os.path.realpath(os.path.join(worktree_path, path_entry))
        if full != worktree_real and not full.startswith(worktree_real + os.sep):
            logger.warning(
                "Rejecting artifact_path %r for task %s: outside worktree",
                path_entry, task_id,
            )
            continue
        try:
            dest = store.ingest_file(group_id, task_id, full)
        except Exception as exc:
            logger.warning(
                "Failed to ingest artifact %r for task %s: %s",
                path_entry, task_id, exc,
            )
            continue
        if dest:
            ingested.append(os.path.basename(dest))
    if ingested:
        logger.info(
            "Ingested %d artifact(s) for task %s: %s",
            len(ingested), task_id, ingested,
        )
    return ingested


@router.post("/mcp/tools/complete_task")
async def mcp_complete_task(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Agent calls this when task is done. Creates approval request if needed."""
    token = _get_token(authorization)
    artifact_paths = body.get("artifact_paths", [])
    summary = body.get("summary", "")
    task_id = body.get("task_id", "")
    group_id = body.get("group_id", "")
    agent_role = body.get("agent_role", "")
    approval_mode = body.get("approval_mode", "auto")

    # Ingest declared artifact_paths into the artifact_store regardless
    # of approval mode, so the dashboard's artifact viewer can find them
    # later even after the worktree resets between tasks. Done before
    # the auto/manual branch because both paths benefit.
    ingested = await _ingest_artifact_paths(
        task_id=task_id, group_id=group_id, artifact_paths=artifact_paths,
    )

    if approval_mode == "auto":
        # Actually mark the task as completed in the DB and resolve deps
        if _task_board:
            try:
                await _task_board.complete_task_with_output(task_id, summary)
                logger.info("Task %s auto-completed via MCP", task_id)
            except ValueError:
                logger.warning("Task %s not found or not in_progress for auto-complete", task_id)
        return {
            "status": "approved",
            "message": "Auto-approved",
            "ingested_artifacts": ingested,
        }

    if approval_mode == "first_run" and _interaction_mgr:
        already_approved = await _interaction_mgr.check_first_run(group_id, agent_role)
        if already_approved:
            return {"status": "approved", "message": "First-run already approved for this group"}

    # Create approval interaction request
    if _interaction_mgr:
        req = await _interaction_mgr.create_request(
            task_id=task_id, group_id=group_id, agent_role=agent_role,
            instance_token=token, req_type="approval",
            request_data={"artifact_paths": artifact_paths, "summary": summary},
            request_key=f"{task_id}:approval:0",
        )
        return {"status": "pending", "request_id": req["id"], "message": "Awaiting human approval"}

    return {"status": "approved", "message": "No interaction manager configured"}


@router.post("/mcp/tools/request_clarification")
async def mcp_request_clarification(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Agent asks user a question. Creates clarification request."""
    token = _get_token(authorization)
    question = body.get("question", "")
    context = body.get("context", "")
    suggested_options = body.get("suggested_options", [])
    task_id = body.get("task_id", "")
    group_id = body.get("group_id", "")
    agent_role = body.get("agent_role", "")

    if _interaction_mgr:
        req = await _interaction_mgr.create_request(
            task_id=task_id, group_id=group_id, agent_role=agent_role,
            instance_token=token, req_type="clarification",
            request_data={"question": question, "context": context, "suggested_options": suggested_options},
        )
        return {"status": "pending", "request_id": req["id"]}

    raise HTTPException(500, "Interaction manager not configured")


@router.post("/mcp/tools/ask_question")
async def mcp_ask_question(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Structured-options clarification with per-role auto / manual mode.

    In ``auto`` mode (default), the agent's preferred_answer is
    persisted with selected_by="agent" and the call returns
    immediately. In ``manual`` mode the call blocks indefinitely
    until either a human submits an answer via
    ``POST /api/questions/{id}/answer`` or the task is cancelled.

    Mode is read from the role's ``clarification_mode`` config.
    Enforces ``max_clarification_requests`` per (task, role); the
    n+1th call returns 429.

    Design:
    docs/superpowers/specs/2026-04-25-agent-questions-design.md
    """
    _get_token(authorization)
    task_id = body.get("task_id") or ""
    group_id = body.get("group_id") or ""
    agent_role = body.get("agent_role") or ""
    question = body.get("question") or ""
    options = body.get("options") or []
    preferred_answer = body.get("preferred_answer") or ""
    reasoning = body.get("reasoning") or ""

    for label, value in (
        ("task_id", task_id), ("group_id", group_id),
        ("agent_role", agent_role),
    ):
        if not isinstance(value, str) or not value:
            raise HTTPException(400, f"{label} is required")

    if not _orchestrator_getter:
        raise HTTPException(503, "orchestrator not wired")
    orch = _orchestrator_getter()
    qmgr = getattr(orch, "agent_question_manager", None) if orch else None
    if qmgr is None:
        raise HTTPException(503, "agent_question_manager not configured")

    # Resolve mode + budget from the role config. Unknown roles fall
    # back to the safe defaults (auto, budget 10).
    role_cfg = (orch.roles or {}).get(agent_role) if orch else None
    if role_cfg is not None:
        mode = getattr(role_cfg, "clarification_mode", "auto") or "auto"
        budget = getattr(role_cfg, "max_clarification_requests", 10) or 10
    else:
        mode = "auto"
        budget = 10

    used = await qmgr.count_for_task(task_id, agent_role)
    if used >= budget:
        if _event_bus is not None:
            try:
                await _event_bus.emit(
                    "task.clarification_budget_exhausted",
                    {
                        "task_id": task_id, "group_id": group_id,
                        "agent_role": agent_role, "budget": budget,
                    },
                )
            except Exception:
                logger.debug("event emit failed", exc_info=True)
        raise HTTPException(
            429,
            f"Clarification budget exhausted ({used}/{budget}); escalating",
        )

    # Identify the asking instance for audit. Best-effort lookup; the
    # MCP layer doesn't authenticate identity, so we trust the task
    # row's claimed_by as the agent's instance_id.
    instance_id = None
    if _task_board is not None:
        try:
            row = await _task_board.get_task(task_id)
            if row:
                instance_id = row.get("claimed_by")
        except Exception:
            pass

    try:
        result = await qmgr.ask(
            task_id=task_id,
            group_id=group_id,
            agent_role=agent_role,
            instance_id=instance_id,
            question=question,
            options=options,
            preferred_answer=preferred_answer,
            reasoning=reasoning,
            mode=mode,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


_VALID_TASK_PRIORITIES = frozenset({"low", "medium", "high", "critical"})
_MAX_MCP_TITLE_LEN = 500
_MAX_MCP_DESCRIPTION_LEN = 20_000


@router.post("/mcp/tools/route_task")
async def mcp_route_task(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Agent routes a task to a connected agent. Validates pipeline edge.

    audit 10 F#29: the endpoint trusts caller-supplied group_id,
    priority and agent_role fields; we harden by validating the
    priority enum and capping the title/description sizes so the
    MCP surface can't be used to smuggle multi-MB payloads into
    the task store or set undefined priority values that bypass
    UI filters. Full agent-role binding requires a token<->role
    table that we don't yet carry; the pipeline-edge check already
    prevents arbitrary ``from -> to`` hops.
    """
    _get_token(authorization)
    target_agent = body.get("target_agent", "")
    task_type = body.get("task_type", "")
    title = body.get("title", "")
    description = body.get("description", "")
    agent_role = body.get("agent_role", "")

    if not isinstance(title, str) or len(title) > _MAX_MCP_TITLE_LEN:
        raise HTTPException(400, f"title must be a string of at most {_MAX_MCP_TITLE_LEN} chars")
    if description is not None and (
        not isinstance(description, str)
        or len(description) > _MAX_MCP_DESCRIPTION_LEN
    ):
        raise HTTPException(
            400, f"description must be a string of at most {_MAX_MCP_DESCRIPTION_LEN} chars",
        )

    # Validate pipeline edge exists
    if _pipeline_getter:
        pipeline = _pipeline_getter()
        valid_targets = [e.to_agent for e in pipeline.edges if e.from_agent == agent_role]
        if target_agent not in valid_targets:
            raise HTTPException(400, f"No pipeline edge from '{agent_role}' to '{target_agent}'. Available targets: {valid_targets}")
        # Check task_type is allowed on the edge
        edge = next((e for e in pipeline.edges if e.from_agent == agent_role and e.to_agent == target_agent), None)
        if edge and edge.task_types and task_type not in edge.task_types:
            raise HTTPException(400, f"Task type '{task_type}' not allowed on edge {agent_role}->{target_agent}. Allowed: {edge.task_types}")

    # Create the task in the TaskBoard if available
    group_id = body.get("group_id")
    priority = body.get("priority", "medium")
    if priority not in _VALID_TASK_PRIORITIES:
        raise HTTPException(
            400, f"invalid priority {priority!r}; expected one of {sorted(_VALID_TASK_PRIORITIES)}",
        )
    blocked_by_task = body.get("task_id")  # calling agent's current task
    chain_id = body.get("chain_id")
    blocked_by = [blocked_by_task] if blocked_by_task else None

    if _task_board:
        task = await _task_board.create_task(
            group_id=group_id or "",
            title=title,
            description=description or None,
            task_type=task_type,
            assigned_to=target_agent,
            created_by=agent_role,
            priority=priority,
            blocked_by=blocked_by,
        )
        logger.info("Task %s routed from %s to %s via MCP", task["id"], agent_role, target_agent)
        return {"status": "ok", "task_id": task["id"], "target": target_agent}

    # Fallback when no task board is configured
    import uuid
    return {"status": "ok", "task_id": f"routed-{uuid.uuid4().hex[:8]}", "target": target_agent}


_VALID_CHECK_STATUS = frozenset({"pass", "fail", "skipped"})
_MAX_CHECK_NAME_LEN = 64
_MAX_CHECK_DETAILS_LEN = 20_000
_MAX_CHECK_COMMAND_LEN = 2_000
_MAX_ARTIFACT_PATH_LEN = 2_048
_MAX_ARTIFACT_PATH_COUNT = 20


@router.post("/mcp/tools/record_check")
async def mcp_record_check(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Record a per-task verification check.

    Writes a ``{check_name: {status, details, duration_ms, command}}``
    entry into ``tasks.completion_checks`` (JSON object column). The
    verification gate in ``complete_and_handoff`` reads this and decides
    whether to merge, re-queue on fail, or flag the task as
    ``merged_unverified`` when nothing was recorded.

    Idempotent by check_name: a second call with the same check_name
    overwrites the prior entry rather than appending, so the agent can
    safely re-run a check after a fix.
    """
    _get_token(authorization)
    task_id = body.get("task_id", "")
    check_name = body.get("check_name", "")
    status = body.get("status", "")
    details = body.get("details")
    duration_ms = body.get("duration_ms")
    command = body.get("command")
    artifact_paths = body.get("artifact_paths")

    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(400, "task_id is required")
    if (
        not isinstance(check_name, str)
        or not check_name
        or len(check_name) > _MAX_CHECK_NAME_LEN
    ):
        raise HTTPException(
            400, f"check_name must be a non-empty string of at most {_MAX_CHECK_NAME_LEN} chars",
        )
    if status not in _VALID_CHECK_STATUS:
        raise HTTPException(
            400, f"status must be one of {sorted(_VALID_CHECK_STATUS)}",
        )
    if details is not None and (
        not isinstance(details, str) or len(details) > _MAX_CHECK_DETAILS_LEN
    ):
        raise HTTPException(
            400, f"details must be a string of at most {_MAX_CHECK_DETAILS_LEN} chars",
        )
    if duration_ms is not None and (
        not isinstance(duration_ms, int) or duration_ms < 0
    ):
        raise HTTPException(400, "duration_ms must be a non-negative int")
    if command is not None and (
        not isinstance(command, str) or len(command) > _MAX_CHECK_COMMAND_LEN
    ):
        raise HTTPException(
            400, f"command must be a string of at most {_MAX_CHECK_COMMAND_LEN} chars",
        )
    # artifact_paths: optional list of paths where the agent saved full
    # stderr / logs. Retry context will render them as "Read <path>"
    # pointers so the next attempt has the actual output, not a summary.
    # Design:
    # docs/superpowers/specs/2026-04-24-structured-failure-feedback-design.md
    if artifact_paths is not None:
        if not isinstance(artifact_paths, list):
            raise HTTPException(
                400, "artifact_paths must be a list of path strings",
            )
        if len(artifact_paths) > _MAX_ARTIFACT_PATH_COUNT:
            raise HTTPException(
                400,
                f"artifact_paths may contain at most "
                f"{_MAX_ARTIFACT_PATH_COUNT} entries",
            )
        for p in artifact_paths:
            if (
                not isinstance(p, str)
                or not p
                or len(p) > _MAX_ARTIFACT_PATH_LEN
            ):
                raise HTTPException(
                    400,
                    "each artifact_paths entry must be a non-empty string "
                    f"of at most {_MAX_ARTIFACT_PATH_LEN} chars",
                )

    if not _task_board:
        raise HTTPException(503, "task_board not configured")

    db = _task_board._db
    existing = await db.execute_fetchone(
        "SELECT completion_checks FROM tasks WHERE id = ?",
        (task_id,),
    )
    if existing is None:
        raise HTTPException(404, f"task not found: {task_id}")

    import json
    raw = existing.get("completion_checks") or "{}"
    try:
        current = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        # Corrupt prior state -- start fresh rather than silently lose the new check.
        current = {}

    entry: dict = {"status": status}
    if details is not None:
        entry["details"] = details
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if command is not None:
        entry["command"] = command
    if artifact_paths is not None:
        entry["artifact_paths"] = list(artifact_paths)
    current[check_name] = entry

    await db.execute(
        "UPDATE tasks SET completion_checks = ? WHERE id = ?",
        (json.dumps(current), task_id),
    )

    if _event_bus:
        try:
            await _event_bus.emit(
                "task.check_recorded",
                {"task_id": task_id, "check_name": check_name, "status": status},
            )
        except Exception:
            logger.debug("task.check_recorded event emit failed", exc_info=True)

    return {"status": "ok", "task_id": task_id, "checks": current}


@router.post("/mcp/tools/get_my_connections")
async def mcp_get_connections(
    body: dict,
    authorization: Optional[str] = Header(None),
):
    """Returns outbound pipeline edges for the requesting agent."""
    _get_token(authorization)
    agent_role = body.get("agent_role", "")

    if _pipeline_getter:
        pipeline = _pipeline_getter()
        connections = [
            {"target": e.to_agent, "task_types": e.task_types}
            for e in pipeline.edges if e.from_agent == agent_role
        ]
        return {"agent_role": agent_role, "connections": connections}

    return {"agent_role": agent_role, "connections": []}


@router.get("/mcp/tools/poll/{request_id}")
async def mcp_poll(request_id: str, authorization: Optional[str] = Header(None)):
    """Long-poll endpoint. Returns current status of an interaction request."""
    _get_token(authorization)
    if _interaction_mgr:
        req = await _interaction_mgr.check_status(request_id)
        if not req:
            raise HTTPException(404, f"Request not found: {request_id}")
        return {"status": req["status"], "response_data": req.get("response_data")}
    raise HTTPException(500, "Interaction manager not configured")
