"""Load and validate team and role configuration from YAML files."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _get_required(data: dict, key: str, context: str = "config") -> Any:
    """Get a required key from a dict, raising ValueError with a clear message."""
    keys = key.split(".")
    current = data
    for k in keys:
        if not isinstance(current, dict) or k not in current:
            raise ValueError(f"Missing required key '{key}' in {context}")
        current = current[k]
    return current


def _validate_range(value: Any, name: str, minimum: int = 1, maximum: int | None = None) -> None:
    """Validate a numeric config value is within bounds."""
    if not isinstance(value, (int, float)) or value < minimum:
        raise ValueError(f"Config '{name}' must be >= {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise ValueError(f"Config '{name}' must be <= {maximum}, got {value!r}")


_REQUIRED_ROLE_KEYS = ["role", "display_name", "prefix", "color", "emoji", "system_prompt"]


# ---------------------------------------------------------------------------
# Team-level configuration
# ---------------------------------------------------------------------------


@dataclass
class AutoScaleDefaults:
    """Default auto-scaling settings applied to roles that don't override."""

    enabled: bool = False
    scale_up_threshold: int = 3
    scale_down_idle: int = 15


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP tool server."""
    builtin: bool = False
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"


@dataclass
class GuardrailsConfig:
    """Guardrails to prevent runaway agent behavior."""
    max_task_depth: int = 10
    max_tasks_per_group: int = 50
    rejection_cycle_limit: int = 3


@dataclass
class TeamConfig:
    """Top-level team settings loaded from config/team.yaml."""

    team_name: str
    db_path: str
    dashboard_host: str
    dashboard_port: int
    artifacts_base_dir: str
    default_max_instances: int
    default_poll_interval: int
    default_idle_timeout: int
    default_auto_scale: AutoScaleDefaults
    group_prefixes: dict[str, str] = field(default_factory=dict)
    cli_provider: str = "claude"
    auth_enabled: bool = False
    auth_tokens: list[str] = field(default_factory=list)
    cost_budgets_enabled: bool = False
    webhooks_enabled: bool = False
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    guardrails: GuardrailsConfig = field(default_factory=GuardrailsConfig)


def load_team_config(path: Path) -> TeamConfig:
    """Load team configuration from a YAML file.

    Parameters
    ----------
    path:
        Path to the team YAML file (e.g. ``config/team.yaml``).

    Returns
    -------
    TeamConfig
        Parsed team configuration.

    Raises
    ------
    FileNotFoundError
        If *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Team config not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    defaults = data.get("defaults", {})
    auto_scale_raw = defaults.get("auto_scale", {})
    auth_raw = data.get("auth", {})
    webhooks_raw = data.get("webhooks", {})

    # Parse MCP servers
    mcp_raw = data.get("mcp_servers", {})
    mcp_servers: dict[str, MCPServerConfig] = {}
    for name, cfg_dict in mcp_raw.items():
        if isinstance(cfg_dict, dict):
            # Fix 4: Skip non-builtin MCP servers with no command
            if not cfg_dict.get("builtin", False) and not cfg_dict.get("command", "").strip():
                logger.warning("MCP server '%s' has no command and is not builtin — skipping", name)
                continue
            mcp_servers[name] = MCPServerConfig(
                builtin=cfg_dict.get("builtin", False),
                command=cfg_dict.get("command", ""),
                args=cfg_dict.get("args", []),
                env=cfg_dict.get("env", {}),
                transport=cfg_dict.get("transport", "stdio"),
            )

    # Ensure built-in servers always exist
    if "task-tools" not in mcp_servers:
        mcp_servers["task-tools"] = MCPServerConfig(builtin=True)
    if "intelligence-tools" not in mcp_servers:
        mcp_servers["intelligence-tools"] = MCPServerConfig(builtin=True)

    # Parse guardrails
    guardrails_raw = data.get("guardrails", {})
    guardrails = GuardrailsConfig(
        max_task_depth=guardrails_raw.get("max_task_depth", 10),
        max_tasks_per_group=guardrails_raw.get("max_tasks_per_group", 50),
        rejection_cycle_limit=guardrails_raw.get("rejection_cycle_limit", 3),
    )

    team_config = TeamConfig(
        team_name=_get_required(data, "team_name", "team.yaml"),
        db_path=str(Path(_get_required(data, "database.path", "team.yaml")).expanduser()),
        dashboard_host=_get_required(data, "dashboard.host", "team.yaml"),
        dashboard_port=_get_required(data, "dashboard.port", "team.yaml"),
        artifacts_base_dir=_get_required(data, "artifacts.base_dir", "team.yaml"),
        default_max_instances=defaults.get("max_instances", 1),
        default_poll_interval=defaults.get("poll_interval_seconds", 5),
        default_idle_timeout=defaults.get("idle_timeout_minutes", 30),
        default_auto_scale=AutoScaleDefaults(
            enabled=auto_scale_raw.get("enabled", False),
            scale_up_threshold=auto_scale_raw.get("scale_up_threshold", 3),
            scale_down_idle=auto_scale_raw.get("scale_down_idle", 15),
        ),
        group_prefixes=data.get("group_prefixes", {}),
        cli_provider=data.get("cli_provider", "claude"),
        auth_enabled=auth_raw.get("enabled", False),
        auth_tokens=auth_raw.get("tokens", []),
        cost_budgets_enabled=data.get("cost_budgets", {}).get("enabled", False),
        webhooks_enabled=webhooks_raw.get("enabled", False),
        mcp_servers=mcp_servers,
        guardrails=guardrails,
    )

    # Fix 2: Numeric bounds validation
    _validate_range(team_config.dashboard_port, "dashboard.port", 1, 65535)
    _validate_range(team_config.default_max_instances, "defaults.max_instances", 1)
    _validate_range(team_config.default_poll_interval, "defaults.poll_interval_seconds", 1)

    return team_config


# ---------------------------------------------------------------------------
# Role-level configuration
# ---------------------------------------------------------------------------


@dataclass
class RouteTarget:
    """A downstream role that this role can create tasks for."""

    role: str
    task_types: list[str] = field(default_factory=list)


@dataclass
class AutoScaleConfig:
    """Per-role auto-scaling configuration."""

    enabled: bool = False
    scale_up_threshold: int = 3
    scale_down_idle: int = 15


@dataclass
class RoleConfig:
    """Full configuration for a single agent role."""

    role: str
    display_name: str
    prefix: str
    color: str
    emoji: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    model: str = "claude-opus-4-6"
    produces: list[str] = field(default_factory=list)
    accepts: list[str] = field(default_factory=list)
    routes_to: list[RouteTarget] = field(default_factory=list)
    can_create_groups: bool = False
    group_type: str | None = None
    max_instances: int = 1
    auto_scale: AutoScaleConfig | None = None
    context_includes: list[str] = field(default_factory=list)
    max_execution_time: int = 1800
    max_turns: int | None = None
    routing_mode: str = "open"


def _parse_role(data: dict) -> RoleConfig:
    """Parse a single role YAML dict into a RoleConfig."""

    # Fix 1: Validate required role keys
    for key in _REQUIRED_ROLE_KEYS:
        if key not in data:
            raise ValueError(f"Role config missing required key '{key}' (file may be incomplete)")

    routes_to = [
        RouteTarget(role=r["role"], task_types=r.get("task_types", []))
        for r in data.get("routes_to", [])
    ]

    auto_scale_raw = data.get("auto_scale")
    auto_scale: AutoScaleConfig | None = None
    if auto_scale_raw is not None:
        auto_scale = AutoScaleConfig(
            enabled=auto_scale_raw.get("enabled", False),
            scale_up_threshold=auto_scale_raw.get("scale_up_threshold", 3),
            scale_down_idle=auto_scale_raw.get("scale_down_idle", 15),
        )

    role_cfg = RoleConfig(
        role=data["role"],
        display_name=data["display_name"],
        prefix=data["prefix"],
        color=data["color"],
        emoji=data["emoji"],
        system_prompt=data["system_prompt"],
        tools=data.get("tools", []),
        model=data.get("model", "claude-opus-4-6"),
        produces=data.get("produces", []),
        accepts=data.get("accepts", []),
        routes_to=routes_to,
        can_create_groups=data.get("can_create_groups", False),
        group_type=data.get("group_type"),
        max_instances=data.get("max_instances", 1),
        auto_scale=auto_scale,
        context_includes=data.get("context_includes", []),
        max_execution_time=data.get("max_execution_time", 1800),
        max_turns=data.get("max_turns"),
        routing_mode=data.get("routing_mode", "open"),
    )

    # Fix 2: Numeric bounds validation for role-specific fields
    if role_cfg.max_turns is not None:
        _validate_range(role_cfg.max_turns, "max_turns", 1)
    if role_cfg.max_execution_time is not None:
        _validate_range(role_cfg.max_execution_time, "max_execution_time", 1)

    return role_cfg


def load_roles(roles_dir: Path) -> dict[str, RoleConfig]:
    """Load all role YAML files from a directory.

    Parameters
    ----------
    roles_dir:
        Path to the directory containing ``*.yaml`` role files.

    Returns
    -------
    dict[str, RoleConfig]
        Mapping of role name to its configuration.
    """
    roles: dict[str, RoleConfig] = {}
    if not roles_dir.is_dir():
        return roles

    for yaml_file in sorted(roles_dir.glob("*.yaml")):
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            if data is None:
                logger.warning("Skipping empty role file: %s", yaml_file.name)
                continue
            role_cfg = _parse_role(data)
            roles[role_cfg.role] = role_cfg
        except (ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
            logger.warning("Skipping invalid role file %s: %s", yaml_file.name, exc)
            continue

    return roles


# ---------------------------------------------------------------------------
# Routing validation
# ---------------------------------------------------------------------------


def validate_routing(roles: dict[str, RoleConfig]) -> list[str]:
    """Validate routing consistency across all loaded roles.

    Checks performed:
    1. Every ``routes_to`` target references an existing role.
    2. At least one role has no inbound routes (i.e. is an entry point).
    3. No two roles share the same ``prefix``.

    Parameters
    ----------
    roles:
        Mapping returned by :func:`load_roles`.

    Returns
    -------
    list[str]
        A list of error messages.  An empty list means routing is valid.
    """
    errors: list[str] = []

    # 1. Check that all route targets exist
    for role_name, role_cfg in roles.items():
        for route in role_cfg.routes_to:
            if route.role not in roles:
                errors.append(
                    f"Role '{role_name}' routes to unknown role '{route.role}'"
                )

    # 2. Check for at least one entry point (a role that no other role routes to)
    all_role_names = set(roles.keys())
    routed_to: set[str] = set()
    for role_cfg in roles.values():
        for route in role_cfg.routes_to:
            routed_to.add(route.role)

    entry_points = all_role_names - routed_to
    if not entry_points and roles:
        errors.append("No entry-point role found (every role is a route target)")

    # 3. Check for duplicate prefixes
    prefix_to_role: dict[str, str] = {}
    for role_name, role_cfg in roles.items():
        if role_cfg.prefix in prefix_to_role:
            errors.append(
                f"Duplicate prefix '{role_cfg.prefix}' "
                f"used by '{prefix_to_role[role_cfg.prefix]}' and '{role_name}'"
            )
        else:
            prefix_to_role[role_cfg.prefix] = role_name

    # 4. Routing cycles are allowed (feedback loops like verifier → coder
    #    are a normal part of multi-agent pipelines).  Self-routes are also
    #    valid (e.g. architect reviewing its own designs).

    return errors
