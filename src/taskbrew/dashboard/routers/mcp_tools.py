"""MCP tool endpoints for agent-orchestrator communication.

Audit 10 F#2 note: _get_token previously only verified the ``Bearer ``
prefix and returned the suffix verbatim. Any caller could invoke any
/mcp/tools/* endpoint with a header like ``Authorization: Bearer x`` and
be treated as authenticated. _get_token now delegates suffix verification
to the shared :class:`AuthManager` when auth is enabled.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Header
from typing import Optional

router = APIRouter()
logger = logging.getLogger(__name__)

_interaction_mgr = None
_pipeline_getter = None
_task_board = None
_auth_manager = None
_auth_warning_emitted = False


def set_mcp_deps(interaction_mgr, pipeline_getter, task_board=None, auth_manager=None):
    """Set dependencies. Called from app.py startup.

    *auth_manager* is optional for backward compatibility. When supplied
    (strongly recommended) _get_token validates the bearer token suffix
    against the configured AuthManager; without it, a log warning is
    emitted on first use and any non-empty suffix is accepted (legacy
    behavior).
    """
    global _interaction_mgr, _pipeline_getter, _task_board, _auth_manager
    _interaction_mgr = interaction_mgr
    _pipeline_getter = pipeline_getter
    _task_board = task_board
    _auth_manager = auth_manager


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

    if approval_mode == "auto":
        # Actually mark the task as completed in the DB and resolve deps
        if _task_board:
            try:
                await _task_board.complete_task_with_output(task_id, summary)
                logger.info("Task %s auto-completed via MCP", task_id)
            except ValueError:
                logger.warning("Task %s not found or not in_progress for auto-complete", task_id)
        return {"status": "approved", "message": "Auto-approved"}

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
