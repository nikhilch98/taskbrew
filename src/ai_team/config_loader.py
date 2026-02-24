"""Load and validate team and role configuration from YAML files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
    approval_required: list[str] = field(default_factory=list)
    group_prefixes: dict[str, str] = field(default_factory=dict)


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

    return TeamConfig(
        team_name=data["team_name"],
        db_path=data["database"]["path"],
        dashboard_host=data["dashboard"]["host"],
        dashboard_port=data["dashboard"]["port"],
        artifacts_base_dir=data["artifacts"]["base_dir"],
        default_max_instances=defaults.get("max_instances", 1),
        default_poll_interval=defaults.get("poll_interval_seconds", 5),
        default_idle_timeout=defaults.get("idle_timeout_minutes", 30),
        default_auto_scale=AutoScaleDefaults(
            enabled=auto_scale_raw.get("enabled", False),
            scale_up_threshold=auto_scale_raw.get("scale_up_threshold", 3),
            scale_down_idle=auto_scale_raw.get("scale_down_idle", 15),
        ),
        approval_required=data.get("approval_required", []),
        group_prefixes=data.get("group_prefixes", {}),
    )


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
    produces: list[str] = field(default_factory=list)
    accepts: list[str] = field(default_factory=list)
    routes_to: list[RouteTarget] = field(default_factory=list)
    can_create_groups: bool = False
    group_type: str | None = None
    max_instances: int = 1
    auto_scale: AutoScaleConfig | None = None
    requires_approval: list[str] = field(default_factory=list)
    context_includes: list[str] = field(default_factory=list)


def _parse_role(data: dict) -> RoleConfig:
    """Parse a single role YAML dict into a RoleConfig."""

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

    return RoleConfig(
        role=data["role"],
        display_name=data["display_name"],
        prefix=data["prefix"],
        color=data["color"],
        emoji=data["emoji"],
        system_prompt=data["system_prompt"],
        tools=data.get("tools", []),
        produces=data.get("produces", []),
        accepts=data.get("accepts", []),
        routes_to=routes_to,
        can_create_groups=data.get("can_create_groups", False),
        group_type=data.get("group_type"),
        max_instances=data.get("max_instances", 1),
        auto_scale=auto_scale,
        requires_approval=data.get("requires_approval", []),
        context_includes=data.get("context_includes", []),
    )


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
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        if data is None:
            continue
        role_cfg = _parse_role(data)
        roles[role_cfg.role] = role_cfg

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

    return errors
