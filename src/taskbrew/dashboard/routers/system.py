"""System routes: server management, projects, settings, webhooks,
notifications, cost budgets, A/B tests, and configuration."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from taskbrew.config_loader import (
    AutoScaleConfig, RouteTarget, _parse_role, validate_routing,
)
from taskbrew.dashboard.models import (
    CreateAbTestBody,
    CreateBudgetBody,
    CreateNotificationBody,
    CreateProjectBody,
    CreateRoleBody,
    CreateWebhookBody,
    UpdateRoleSettingsBody,
    UpdateTeamSettingsBody,
)
from taskbrew.dashboard.routers._deps import get_orch, get_orch_optional, set_orchestrator

router = APIRouter()

# ------------------------------------------------------------------
# Placeholders for auth dependencies -- set by app.py
# ------------------------------------------------------------------
_verify_admin = None


def set_auth_deps(verify_admin):
    """Called by app.py to inject auth dependency callables."""
    global _verify_admin
    _verify_admin = verify_admin


# ------------------------------------------------------------------
# Server restart (registered directly on the app by app.py for auth deps)
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Projects  (needs project_manager from app.py)
# ------------------------------------------------------------------
_project_manager = None
_broadcast_event = None


def set_project_deps(project_manager, broadcast_event):
    """Called by app.py to inject project_manager and broadcast callback."""
    global _project_manager, _broadcast_event
    _project_manager = project_manager
    _broadcast_event = broadcast_event


@router.get("/api/projects/status")
async def project_status():
    if not _project_manager:
        return {"has_manager": False, "has_projects": False, "active": None}
    projects = _project_manager.list_projects()
    active = _project_manager.get_active()
    return {
        "has_manager": True,
        "has_projects": len(projects) > 0,
        "project_count": len(projects),
        "active": active,
    }


@router.get("/api/projects")
async def list_projects():
    if not _project_manager:
        return []
    return _project_manager.list_projects()


@router.post("/api/projects")
async def create_project(body: CreateProjectBody):
    if not _project_manager:
        raise HTTPException(500, "Project manager not initialized")
    name = body.name.strip()
    directory = body.directory.strip()
    with_defaults = body.with_defaults
    if not name:
        raise HTTPException(400, "Project name is required")
    if not directory:
        raise HTTPException(400, "Project directory is required")
    cli_provider = getattr(body, "cli_provider", "claude") or "claude"
    try:
        result = _project_manager.create_project(
            name, directory, with_defaults=with_defaults, cli_provider=cli_provider,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.post("/api/browse-directory")
async def browse_directory():
    """Open a native OS folder picker dialog and return the selected path."""
    import sys

    if sys.platform != "darwin":
        raise HTTPException(
            501,
            "Native folder picker is only supported on macOS. "
            "Please type the path manually.",
        )

    script = 'POSIX path of (choose folder with prompt "Select project directory")'
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(408, "Folder picker timed out")

    if proc.returncode != 0:
        error_msg = stderr.decode().strip()
        if "User canceled" in error_msg or "(-128)" in error_msg:
            return {"path": None, "cancelled": True}
        raise HTTPException(500, f"Folder picker failed: {error_msg}")

    selected = stdout.decode().strip().rstrip("/")
    if not selected:
        return {"path": None, "cancelled": True}

    return {"path": selected, "cancelled": False}


@router.delete("/api/projects/{project_id}")
async def remove_project(project_id: str):
    if not _project_manager:
        raise HTTPException(500, "Project manager not initialized")
    try:
        _project_manager.delete_project(project_id)
    except KeyError:
        raise HTTPException(404, f"Project '{project_id}' not found")
    return {"status": "ok"}


@router.post("/api/projects/{project_id}/activate")
async def activate_project_endpoint(project_id: str):
    if not _project_manager:
        raise HTTPException(500, "Project manager not initialized")
    try:
        orch = await _project_manager.activate_project(project_id)
    except KeyError:
        raise HTTPException(404, f"Project '{project_id}' not found")
    except FileNotFoundError as e:
        raise HTTPException(410, str(e))

    # Update the global orchestrator reference so all API endpoints use the new one
    set_orchestrator(orch)

    # Re-subscribe to events for new project
    if _broadcast_event:
        orch.event_bus.subscribe("*", _broadcast_event)

    # Start agents
    from taskbrew.main import start_agents
    await start_agents(orch)

    return {"status": "ok", "project": _project_manager.get_active()}


@router.get("/api/projects/active")
async def get_active_project():
    if not _project_manager:
        return None
    return _project_manager.get_active()


@router.post("/api/projects/active/deactivate")
async def deactivate_project():
    if not _project_manager:
        raise HTTPException(500, "Project manager not initialized")
    await _project_manager.deactivate_current()
    # Clear the global orchestrator reference so endpoints know there is no active project
    set_orchestrator(None)
    return {"status": "ok"}


# ------------------------------------------------------------------
# Settings
# ------------------------------------------------------------------


@router.get("/api/settings/team")
async def get_team_settings():
    orch = get_orch_optional()
    tc = orch.team_config if orch else None
    pd = orch.project_dir if orch else None
    if not tc:
        return {}
    return {
        "name": tc.team_name,
        "project_dir": pd,
        "default_model": getattr(tc, "default_model", ""),
        "db_path": tc.db_path,
        "dashboard_host": tc.dashboard_host,
        "dashboard_port": tc.dashboard_port,
        "default_poll_interval": tc.default_poll_interval,
        "default_idle_timeout": tc.default_idle_timeout,
        "default_max_instances": tc.default_max_instances,
        "auth_enabled": tc.auth_enabled,
        "cost_budgets_enabled": tc.cost_budgets_enabled,
        "webhooks_enabled": tc.webhooks_enabled,
        "group_prefixes": tc.group_prefixes,
    }


@router.put("/api/settings/team")
async def update_team_settings(body: UpdateTeamSettingsBody):
    orch = get_orch()
    body = body.model_dump(exclude_none=True)
    tc_s = orch.team_config
    pd = orch.project_dir
    if not tc_s:
        raise HTTPException(status_code=404, detail="No team config loaded")

    # Update in-memory config
    if "name" in body:
        tc_s.team_name = body["name"]
    if "auth_enabled" in body:
        tc_s.auth_enabled = body["auth_enabled"]
    if "cost_budgets_enabled" in body:
        tc_s.cost_budgets_enabled = body["cost_budgets_enabled"]
    if "webhooks_enabled" in body:
        tc_s.webhooks_enabled = body["webhooks_enabled"]
    if "default_poll_interval" in body:
        tc_s.default_poll_interval = body["default_poll_interval"]
    if "default_idle_timeout" in body:
        tc_s.default_idle_timeout = body["default_idle_timeout"]
    if "default_max_instances" in body:
        tc_s.default_max_instances = body["default_max_instances"]
    if "group_prefixes" in body:
        tc_s.group_prefixes = body["group_prefixes"]

    # Persist to YAML file
    if pd:
        yaml_path = Path(pd) / "config" / "team.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}

            if "name" in body:
                data["team_name"] = body["name"]

            # Defaults section
            defaults = data.setdefault("defaults", {})
            if "default_poll_interval" in body:
                defaults["poll_interval_seconds"] = body["default_poll_interval"]
            if "default_idle_timeout" in body:
                defaults["idle_timeout_minutes"] = body["default_idle_timeout"]
            if "default_max_instances" in body:
                defaults["max_instances"] = body["default_max_instances"]

            # Top-level fields
            if "group_prefixes" in body:
                data["group_prefixes"] = body["group_prefixes"]

            # Nested config sections
            if "auth_enabled" in body:
                auth = data.setdefault("auth", {})
                auth["enabled"] = body["auth_enabled"]
            if "cost_budgets_enabled" in body:
                cb = data.setdefault("cost_budgets", {})
                cb["enabled"] = body["cost_budgets_enabled"]
            if "webhooks_enabled" in body:
                wh = data.setdefault("webhooks", {})
                wh["enabled"] = body["webhooks_enabled"]

            with open(yaml_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"status": "ok"}


@router.get("/api/settings/roles")
async def get_roles_settings():
    orch = get_orch_optional()
    _roles = orch.roles if orch else None
    if not _roles:
        return []
    result = []
    for name, rc in _roles.items():
        result.append({
            "role": name,
            "display_name": rc.display_name,
            "system_prompt": rc.system_prompt,
            "model": rc.model,
            "tools": rc.tools,
            "max_instances": rc.max_instances,
            "prefix": rc.prefix,
            "color": rc.color,
            "emoji": rc.emoji,
            "routes_to": [
                {"role": rt.role, "task_types": rt.task_types}
                for rt in rc.routes_to
            ],
            "produces": rc.produces,
            "accepts": rc.accepts,
            "can_create_groups": rc.can_create_groups,
            "group_type": rc.group_type,
            "context_includes": rc.context_includes,
            "auto_scale": {
                "enabled": rc.auto_scale.enabled,
                "scale_up_threshold": rc.auto_scale.scale_up_threshold,
                "scale_down_idle": rc.auto_scale.scale_down_idle,
            } if rc.auto_scale else None,
            "max_turns": rc.max_turns,
            "max_execution_time": rc.max_execution_time,
        })
    return result


@router.put("/api/settings/roles/{role_name}")
async def update_role_settings(role_name: str, body: UpdateRoleSettingsBody):
    body = body.model_dump(exclude_none=True)
    # original logic below uses body as dict
    orch = get_orch()
    _roles = orch.roles
    _project_dir = orch.project_dir
    if not _roles or role_name not in _roles:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")
    rc = _roles[role_name]

    # Snapshot the role config before mutation so we can roll back on
    # validation failure.
    rc_snapshot = copy.deepcopy(rc)

    # Identity fields
    if "display_name" in body:
        rc.display_name = body["display_name"]
    if "prefix" in body:
        rc.prefix = body["prefix"]
    if "color" in body:
        rc.color = body["color"]
    if "emoji" in body:
        rc.emoji = body["emoji"]

    # Core fields
    if "system_prompt" in body:
        rc.system_prompt = body["system_prompt"]
    if "model" in body:
        rc.model = body["model"]
    if "tools" in body:
        rc.tools = body["tools"]

    # Execution fields
    if "max_instances" in body:
        rc.max_instances = body["max_instances"]
    if "max_turns" in body:
        rc.max_turns = body["max_turns"]
    if "max_execution_time" in body:
        rc.max_execution_time = body["max_execution_time"]

    # List fields
    if "produces" in body:
        rc.produces = body["produces"]
    if "accepts" in body:
        rc.accepts = body["accepts"]
    if "context_includes" in body:
        rc.context_includes = body["context_includes"]

    # Group settings
    if "can_create_groups" in body:
        rc.can_create_groups = body["can_create_groups"]
    if "group_type" in body:
        rc.group_type = body["group_type"]

    # Routes -- convert dicts to RouteTarget objects
    if "routes_to" in body:
        rc.routes_to = [
            RouteTarget(role=r["role"], task_types=r.get("task_types", []))
            for r in body["routes_to"]
        ]

    # Auto-scale -- convert dict to AutoScaleConfig
    if "auto_scale" in body:
        asc = body["auto_scale"]
        if asc is None:
            rc.auto_scale = None
        else:
            rc.auto_scale = AutoScaleConfig(
                enabled=asc.get("enabled", False),
                scale_up_threshold=asc.get("scale_up_threshold", 3),
                scale_down_idle=asc.get("scale_down_idle", 15),
            )

    # Validate routing after in-memory updates.  If the new settings
    # introduce invalid routes (e.g. routing to a non-existent role or
    # duplicate prefixes) we roll back the in-memory changes and return
    # a 400 error *without* persisting anything to disk.
    routing_errors = validate_routing(_roles)
    if routing_errors:
        # Roll back — restore the pre-mutation snapshot.
        _roles[role_name] = rc_snapshot
        raise HTTPException(
            status_code=400,
            detail={"validation_errors": routing_errors},
        )

    # Persist to YAML file
    if _project_dir:
        yaml_path = Path(_project_dir) / "config" / "roles" / f"{role_name}.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                data = yaml.safe_load(f) or {}

            # Simple scalar/list fields that map directly
            direct_keys = (
                "display_name", "prefix", "color", "emoji",
                "system_prompt", "model", "tools",
                "max_instances", "max_turns", "max_execution_time",
                "produces", "accepts",
                "context_includes", "can_create_groups", "group_type",
            )
            for key in direct_keys:
                if key in body:
                    data[key] = body[key]

            # routes_to -- serialize RouteTarget objects back to dicts
            if "routes_to" in body:
                data["routes_to"] = body["routes_to"]

            # auto_scale -- serialize AutoScaleConfig back to dict
            if "auto_scale" in body:
                data["auto_scale"] = body["auto_scale"]

            with open(yaml_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    return {"status": "ok", "role": role_name}


@router.post("/api/settings/roles")
async def create_role(body: CreateRoleBody):
    body = body.model_dump(exclude_none=True)
    orch = get_orch()
    _roles = orch.roles
    _project_dir = orch.project_dir
    role_name = body.get("role", "").strip()
    if not role_name:
        raise HTTPException(status_code=400, detail="role name is required")
    if not re.match(r"^[a-z][a-z0-9_]*$", role_name):
        raise HTTPException(
            status_code=400,
            detail="Role name must be lowercase alphanumeric (underscores allowed, must start with a letter)",
        )
    if _roles and role_name in _roles:
        raise HTTPException(status_code=409, detail=f"Role '{role_name}' already exists")

    # Build YAML data with defaults for missing fields
    yaml_data: dict[str, Any] = {
        "role": role_name,
        "display_name": body.get("display_name", role_name.title()),
        "prefix": body.get("prefix", role_name[:2].upper()),
        "color": body.get("color", "#6b7280"),
        "emoji": body.get("emoji", "\U0001F916"),
        "system_prompt": body.get("system_prompt", f"You are the {role_name} agent."),
        "tools": body.get("tools", []),
        "model": body.get("model", "claude-sonnet-4-6"),
        "produces": body.get("produces", []),
        "accepts": body.get("accepts", []),
        "routes_to": body.get("routes_to", []),
        "can_create_groups": body.get("can_create_groups", False),
        "max_instances": body.get("max_instances", 1),
        "context_includes": body.get("context_includes", []),
        "max_execution_time": body.get("max_execution_time", 1800),
    }
    if body.get("group_type"):
        yaml_data["group_type"] = body["group_type"]
    if body.get("max_turns"):
        yaml_data["max_turns"] = body["max_turns"]
    if body.get("auto_scale"):
        yaml_data["auto_scale"] = body["auto_scale"]

    # Write YAML file
    if _project_dir:
        roles_dir = Path(_project_dir) / "config" / "roles"
        roles_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = roles_dir / f"{role_name}.yaml"
        with open(yaml_path, "w") as f:
            yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Parse and register in memory
    rc = _parse_role(yaml_data)
    if _roles is not None:
        _roles[role_name] = rc

    return {"status": "ok", "role": role_name}


@router.delete("/api/settings/roles/{role_name}")
async def delete_role(role_name: str):
    orch = get_orch()
    _roles = orch.roles
    _project_dir = orch.project_dir
    if not _roles or role_name not in _roles:
        raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")

    # Remove YAML file
    if _project_dir:
        yaml_path = Path(_project_dir) / "config" / "roles" / f"{role_name}.yaml"
        if yaml_path.exists():
            yaml_path.unlink()

    # Capture the deleted role's config before removing it so we can
    # clean up task_board prefix mappings and agent loops.
    deleted_rc = _roles[role_name]

    # Remove from in-memory roles
    del _roles[role_name]

    # Clean up routes_to in other roles that pointed to the deleted role
    for other_name, other_rc in _roles.items():
        original_len = len(other_rc.routes_to)
        other_rc.routes_to = [
            rt for rt in other_rc.routes_to if rt.role != role_name
        ]
        if len(other_rc.routes_to) < original_len and _project_dir:
            # Persist the route cleanup to YAML
            other_yaml = Path(_project_dir) / "config" / "roles" / f"{other_name}.yaml"
            if other_yaml.exists():
                with open(other_yaml) as f:
                    data = yaml.safe_load(f) or {}
                data["routes_to"] = [
                    {"role": rt.role, "task_types": rt.task_types}
                    for rt in other_rc.routes_to
                ]
                with open(other_yaml, "w") as f:
                    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # Clean up task_board prefix tracking for the deleted role
    tb = orch.task_board
    tb._role_to_prefix.pop(role_name, None)
    if deleted_rc.can_create_groups:
        tb._group_prefixes.pop(role_name, None)

    # Stop any running agent loops for the deleted role.
    # Instance IDs follow the pattern "{role_name}-{N}" or
    # "{role_name}-auto-{N}" for auto-scaled instances.
    if hasattr(orch, "_agent_tasks_by_id"):
        ids_to_stop = [
            iid for iid in orch._agent_tasks_by_id
            if iid == role_name or iid.startswith(f"{role_name}-")
        ]
        for iid in ids_to_stop:
            entry = orch._agent_tasks_by_id.pop(iid, None)
            if entry:
                agent_loop, agent_task = entry
                agent_loop.stop()
                agent_task.cancel()
                if agent_loop in orch._agent_loops:
                    orch._agent_loops.remove(agent_loop)
                if agent_task in orch.agent_tasks:
                    orch.agent_tasks.remove(agent_task)

    return {"status": "ok", "role": role_name}


@router.post("/api/settings/validate")
async def validate_settings():
    orch = get_orch_optional()
    _roles = orch.roles if orch else None
    if not _roles:
        return {"valid": True, "errors": []}
    errors = validate_routing(_roles)
    return {"valid": len(errors) == 0, "errors": errors}


@router.get("/api/settings/models")
async def get_available_models(provider: str = ""):
    orch = get_orch_optional()
    active_provider = provider
    if not active_provider and orch and hasattr(orch.team_config, "cli_provider"):
        active_provider = orch.team_config.cli_provider
    active_provider = active_provider or "claude"

    if active_provider == "gemini":
        return [
            {"id": "gemini-3.1-pro-preview", "label": "Flagship"},
            {"id": "gemini-3-flash-preview", "label": "Balanced"},
        ]
    return [
        {"id": "claude-opus-4-6", "label": "Flagship"},
        {"id": "claude-sonnet-4-6", "label": "Balanced"},
        {"id": "claude-haiku-4-5-20251001", "label": "Fast"},
    ]


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------


@router.get("/api/notifications")
async def get_notifications(limit: int = 50):
    orch = get_orch()
    return await orch.task_board._db.get_unread_notifications(limit)


@router.post("/api/notifications")
async def create_notification(body: CreateNotificationBody):
    orch = get_orch()
    notif = await orch.task_board._db.create_notification(
        type=body.type,
        title=body.title,
        message=body.message,
        severity=body.severity,
        data=body.data,
    )
    return notif


@router.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int):
    orch = get_orch()
    await orch.task_board._db.mark_notification_read(notification_id)
    return {"status": "ok"}


@router.post("/api/notifications/read-all")
async def mark_all_read():
    orch = get_orch()
    await orch.task_board._db.mark_all_notifications_read()
    return {"status": "ok"}


# ------------------------------------------------------------------
# Cost Budgets
# ------------------------------------------------------------------


@router.get("/api/budgets")
async def get_budgets():
    """List all cost budgets."""
    orch = get_orch()
    return await orch.task_board._db.execute_fetchall("SELECT * FROM cost_budgets ORDER BY scope")


@router.post("/api/budgets")
async def create_budget(body: CreateBudgetBody):
    orch = get_orch()
    import uuid
    budget_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc)
    scope = body.scope
    budget_usd = body.budget_usd
    period = body.period
    scope_id = body.scope_id

    if period == "daily":
        reset_at = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0).isoformat()
    elif period == "weekly":
        reset_at = (now + timedelta(days=7)).isoformat()
    else:
        reset_at = None

    await orch.task_board._db.execute(
        "INSERT INTO cost_budgets (id, scope, scope_id, budget_usd, period, reset_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (budget_id, scope, scope_id, budget_usd, period, reset_at, now.isoformat())
    )
    return {"id": budget_id, "scope": scope, "budget_usd": budget_usd}


@router.delete("/api/budgets/{budget_id}")
async def delete_budget(budget_id: str):
    orch = get_orch()
    await orch.task_board._db.execute("DELETE FROM cost_budgets WHERE id = ?", (budget_id,))
    return {"status": "ok"}


# ------------------------------------------------------------------
# Webhooks
# ------------------------------------------------------------------


@router.get("/api/webhooks")
async def get_webhooks():
    orch = get_orch()
    return await orch.task_board._db.execute_fetchall("SELECT id, url, events, active, created_at, last_triggered_at FROM webhooks")


@router.post("/api/webhooks")
async def create_webhook(body: CreateWebhookBody):
    orch = get_orch()
    import uuid
    webhook_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    url = body.url
    events = body.events
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    await orch.task_board._db.execute(
        "INSERT INTO webhooks (id, url, events, secret, created_at) VALUES (?, ?, ?, ?, ?)",
        (webhook_id, url, ",".join(events), body.secret, now)
    )
    return {"id": webhook_id, "url": url, "events": events}


@router.delete("/api/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: str):
    orch = get_orch()
    await orch.task_board._db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
    return {"status": "ok"}


# ------------------------------------------------------------------
# A/B Test Configs
# ------------------------------------------------------------------


@router.get("/api/ab-tests")
async def get_ab_tests():
    orch = get_orch()
    return await orch.task_board._db.execute_fetchall("SELECT * FROM ab_test_configs ORDER BY created_at DESC")


@router.post("/api/ab-tests")
async def create_ab_test(body: CreateAbTestBody):
    orch = get_orch()
    import uuid
    test_id = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    await orch.task_board._db.execute(
        "INSERT INTO ab_test_configs (id, name, role, variant_a, variant_b, allocation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (test_id, body.name, body.role, json.dumps(body.variant_a), json.dumps(body.variant_b), body.allocation, now)
    )
    return {"id": test_id, "name": body.name}
