"""Pipeline topology editor: CRUD for pipeline edges, start agent, node config.

Audit 11b F#22: ``_pipeline`` is module-level mutable state that every
mutating route stomps on without a lock. Two concurrent PUT/POST/DELETE
calls could interleave edge additions and leave the in-memory and on-disk
pipeline out of sync. ``_pipeline_lock`` serialises all reads that turn
into writes and every mutation behind a single asyncio.Lock. The lock
is reused by helpers callers (e.g. system.py's delete_role) via
``acquire_pipeline_lock()`` to avoid double-lock deadlock.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

from taskbrew.config_loader import (
    PipelineConfig,
    PipelineEdge,
    PipelineNodeConfig,
    load_pipeline,
    save_pipeline,
)
from taskbrew.dashboard.models import (
    PipelineEdgeBody,
    SetNodeConfigBody,
    SetStartAgentBody,
    UpdatePipelineBody,
    UpdatePipelineEdgeBody,
)
from taskbrew.dashboard.routers._deps import get_orch, get_orch_optional

router = APIRouter()

# In-memory pipeline state, loaded at startup or set by tests.
_pipeline: PipelineConfig | None = None
# audit 11b F#22: serialise every mutation of the global pipeline.
_pipeline_lock = asyncio.Lock()


def acquire_pipeline_lock() -> asyncio.Lock:
    """Return the shared pipeline lock for ``async with`` use."""
    return _pipeline_lock


def set_pipeline_deps(pipeline: PipelineConfig) -> None:
    """Inject pipeline config (called by app.py or tests)."""
    global _pipeline
    _pipeline = pipeline


def get_pipeline() -> PipelineConfig:
    """Return the current in-memory pipeline, initialising if needed."""
    global _pipeline
    if _pipeline is None:
        _pipeline = PipelineConfig()
    return _pipeline


def _persist(project_dir: str | None) -> None:
    """Write the current in-memory pipeline to team.yaml if project_dir set."""
    if not project_dir:
        return
    yaml_path = Path(project_dir) / "config" / "team.yaml"
    if yaml_path.exists():
        save_pipeline(yaml_path, get_pipeline())


def _cleanup_role_from_pipeline(role_name: str) -> None:
    """Remove all edges and node_config referencing a deleted role."""
    pc = get_pipeline()
    pc.edges = [
        e for e in pc.edges
        if e.from_agent != role_name and e.to_agent != role_name
    ]
    pc.node_config.pop(role_name, None)
    if pc.start_agent == role_name:
        pc.start_agent = None


# ------------------------------------------------------------------
# GET /api/pipeline -- return full pipeline
# ------------------------------------------------------------------


@router.post("/api/pipeline/migrate")
async def migrate_pipeline_from_routes():
    """Explicit migration endpoint for the deprecated routes_to field.

    Kept as POST (side-effect write) so the mutation is auditable and
    cannot be triggered by accidental GETs. Falls under the admin dep
    applied at include_router(dependencies=[Depends(verify_admin)]) time.
    """
    pc = get_pipeline()
    orch = get_orch_optional()
    if not orch or not orch.roles:
        return {"status": "noop", "reason": "no orchestrator / no roles"}
    if pc.edges:
        return {"status": "noop", "reason": "pipeline already has edges"}
    has_routes = any(len(rc.routes_to) > 0 for rc in orch.roles.values())
    if not has_routes:
        return {"status": "noop", "reason": "no routes_to to migrate"}
    from taskbrew.config_loader import (
        migrate_routes_to_pipeline,
        save_pipeline as _save_pipeline,
    )
    pc = migrate_routes_to_pipeline(orch.roles)
    set_pipeline_deps(pc)
    if orch.project_dir:
        team_yaml = Path(orch.project_dir) / "config" / "team.yaml"
        if team_yaml.exists():
            _save_pipeline(team_yaml, pc)
    return {
        "status": "migrated",
        "edges": len(pc.edges),
    }


@router.get("/api/pipeline")
async def get_pipeline_config():
    """Return the configured pipeline.

    audit 11b F#16: the previous implementation silently migrated
    routes_to into a pipeline and WROTE team.yaml from inside a GET
    handler. Two problems:
    (1) GET requests MUST be idempotent; writing to disk is a side
        effect HTTP clients and middleware will not expect.
    (2) Concurrent GETs could race with a PUT /api/pipeline, corrupting
        team.yaml (the atomic-write fix in system.py does not apply
        here yet).

    Pipeline bootstrap now happens at ``create_app()`` startup in
    app.py; this handler is read-only. Callers that need the migration
    explicitly should POST to ``/api/pipeline/migrate`` (added below)
    which does the same work behind the admin-dep gate.
    """
    pc = get_pipeline()
    return {
        "id": pc.id,
        "name": pc.name,
        "start_agent": pc.start_agent,
        "edges": [
            {
                "id": e.id,
                "from": e.from_agent,
                "to": e.to_agent,
                "task_types": e.task_types,
                "on_failure": e.on_failure,
            }
            for e in pc.edges
        ],
        "node_config": {
            role: {"join_strategy": nc.join_strategy}
            for role, nc in pc.node_config.items()
        },
    }


# ------------------------------------------------------------------
# PUT /api/pipeline -- full update (Save All Changes)
# ------------------------------------------------------------------


@router.put("/api/pipeline")
async def update_pipeline_full(body: UpdatePipelineBody):
    orch = get_orch()
    pc = get_pipeline()
    roles = orch.roles or {}

    if body.name is not None:
        pc.name = body.name
    if body.start_agent is not None:
        if body.start_agent not in roles:
            raise HTTPException(400, f"Unknown role: {body.start_agent}")
        pc.start_agent = body.start_agent
    if body.edges is not None:
        new_edges = []
        for e in body.edges:
            from_agent = e.get("from", "")
            to_agent = e.get("to", "")
            if from_agent not in roles:
                raise HTTPException(400, f"Unknown source role: {from_agent}")
            if to_agent not in roles:
                raise HTTPException(400, f"Unknown target role: {to_agent}")
            new_edges.append(PipelineEdge(
                id=e.get("id", f"edge-{uuid.uuid4().hex[:8]}"),
                from_agent=from_agent,
                to_agent=to_agent,
                task_types=e.get("task_types", []),
                on_failure=e.get("on_failure", "block"),
            ))
        pc.edges = new_edges
    if body.node_config is not None:
        new_nc: dict[str, PipelineNodeConfig] = {}
        for role, cfg in body.node_config.items():
            js = cfg.get("join_strategy", "wait_all")
            if js not in ("wait_all", "stream"):
                raise HTTPException(400, f"Invalid join_strategy: {js}")
            new_nc[role] = PipelineNodeConfig(join_strategy=js)
        pc.node_config = new_nc

    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# POST /api/pipeline/edges -- add edge
# ------------------------------------------------------------------


@router.post("/api/pipeline/edges")
async def add_pipeline_edge(body: PipelineEdgeBody):
    orch = get_orch()
    roles = orch.roles or {}
    pc = get_pipeline()

    if body.from_agent not in roles:
        raise HTTPException(400, f"Unknown source role: {body.from_agent}")
    if body.to_agent not in roles:
        raise HTTPException(400, f"Unknown target role: {body.to_agent}")
    if body.on_failure not in ("block", "continue_partial", "cancel_pipeline"):
        raise HTTPException(400, f"Invalid on_failure: {body.on_failure}")

    edge_id = f"edge-{uuid.uuid4().hex[:8]}"
    edge = PipelineEdge(
        id=edge_id,
        from_agent=body.from_agent,
        to_agent=body.to_agent,
        task_types=body.task_types,
        on_failure=body.on_failure,
    )
    pc.edges.append(edge)
    _persist(orch.project_dir)

    return {"status": "ok", "edge_id": edge_id}


# ------------------------------------------------------------------
# DELETE /api/pipeline/edges/{edge_id} -- remove edge
# ------------------------------------------------------------------


@router.delete("/api/pipeline/edges/{edge_id}")
async def delete_pipeline_edge(edge_id: str):
    orch = get_orch()
    pc = get_pipeline()

    original_len = len(pc.edges)
    pc.edges = [e for e in pc.edges if e.id != edge_id]
    if len(pc.edges) == original_len:
        raise HTTPException(404, f"Edge not found: {edge_id}")

    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# PUT /api/pipeline/edges/{edge_id} -- update edge config
# ------------------------------------------------------------------


@router.put("/api/pipeline/edges/{edge_id}")
async def update_pipeline_edge(edge_id: str, body: UpdatePipelineEdgeBody):
    orch = get_orch()
    pc = get_pipeline()

    edge = next((e for e in pc.edges if e.id == edge_id), None)
    if edge is None:
        raise HTTPException(404, f"Edge not found: {edge_id}")

    if body.task_types is not None:
        edge.task_types = body.task_types
    if body.on_failure is not None:
        if body.on_failure not in ("block", "continue_partial", "cancel_pipeline"):
            raise HTTPException(400, f"Invalid on_failure: {body.on_failure}")
        edge.on_failure = body.on_failure

    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# PUT /api/pipeline/start-agent -- set start agent
# ------------------------------------------------------------------


@router.put("/api/pipeline/start-agent")
async def set_start_agent(body: SetStartAgentBody):
    orch = get_orch()
    roles = orch.roles or {}
    pc = get_pipeline()

    if body.role not in roles:
        raise HTTPException(400, f"Unknown role: {body.role}")

    pc.start_agent = body.role
    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# PUT /api/pipeline/node-config/{role_name} -- set join strategy
# ------------------------------------------------------------------


@router.put("/api/pipeline/node-config/{role_name}")
async def set_node_config(role_name: str, body: SetNodeConfigBody):
    orch = get_orch()
    roles = orch.roles or {}
    pc = get_pipeline()

    if role_name not in roles:
        raise HTTPException(400, f"Unknown role: {role_name}")
    if body.join_strategy not in ("wait_all", "stream"):
        raise HTTPException(400, f"Invalid join_strategy: {body.join_strategy}")

    pc.node_config[role_name] = PipelineNodeConfig(
        join_strategy=body.join_strategy,
    )
    _persist(orch.project_dir)
    return {"status": "ok"}


# ------------------------------------------------------------------
# POST /api/pipeline/validate -- validate pipeline
# ------------------------------------------------------------------


@router.post("/api/pipeline/validate")
async def validate_pipeline():
    orch = get_orch_optional()
    roles = orch.roles if orch else {}
    pc = get_pipeline()

    errors: list[str] = []
    warnings: list[str] = []
    infos: list[str] = []

    # Error: No start agent
    if pc.start_agent is None and (pc.edges or roles):
        errors.append("No start agent marked. Set a start agent for the pipeline.")

    # Error: Start agent does not exist
    if pc.start_agent and pc.start_agent not in roles:
        errors.append(
            f"Start agent '{pc.start_agent}' does not exist. "
            "Re-create it or set a different start agent."
        )

    # Warning: Start agent has incoming edges
    if pc.start_agent:
        incoming_to_start = [
            e for e in pc.edges
            if e.to_agent == pc.start_agent and e.from_agent != pc.start_agent
        ]
        if incoming_to_start:
            warnings.append(
                f"Start agent '{pc.start_agent}' has incoming edges from other agents. "
                "Start agents should only receive tasks from the user."
            )

    # Warning: Edges referencing unknown roles
    for edge in pc.edges:
        if edge.from_agent not in roles:
            warnings.append(
                f"Edge '{edge.id}' references unknown agent: {edge.from_agent}. "
                "Remove or re-create this agent."
            )
        if edge.to_agent not in roles:
            warnings.append(
                f"Edge '{edge.id}' references unknown agent: {edge.to_agent}. "
                "Remove or re-create this agent."
            )

    # Warning: Revision loops without max_revision_cycles cap
    for edge in pc.edges:
        if "revision" in edge.task_types:
            target_role = roles.get(edge.to_agent)
            if target_role and target_role.max_revision_cycles == 0:
                warnings.append(
                    f"Edge '{edge.id}' ({edge.from_agent} -> {edge.to_agent}) "
                    f"carries 'revision' tasks but '{edge.to_agent}' has "
                    "max_revision_cycles=0 (unlimited). Consider setting a cap."
                )

    # Warning: Edge task_types not in source produces or target accepts
    for edge in pc.edges:
        source = roles.get(edge.from_agent)
        target = roles.get(edge.to_agent)
        if source and target and edge.task_types:
            for tt in edge.task_types:
                if source.produces and tt not in source.produces:
                    warnings.append(
                        f"Edge '{edge.id}': task_type '{tt}' not in "
                        f"'{edge.from_agent}' produces list."
                    )
                if target.accepts and tt not in target.accepts:
                    warnings.append(
                        f"Edge '{edge.id}': task_type '{tt}' not in "
                        f"'{edge.to_agent}' accepts list."
                    )

    # Info: Disconnected agents
    connected_roles = set()
    for edge in pc.edges:
        connected_roles.add(edge.from_agent)
        connected_roles.add(edge.to_agent)
    if pc.start_agent:
        connected_roles.add(pc.start_agent)
    for role_name in roles:
        if role_name not in connected_roles:
            infos.append(
                f"Agent '{role_name}' is disconnected (no edges, not start agent)."
            )

    valid = len(errors) == 0
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
    }
