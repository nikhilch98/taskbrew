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
class ExecutionConfig:
    """Orchestrator-level execution settings from team.yaml.

    All fields have sensible defaults — the ``execution`` section can be
    omitted entirely from team.yaml.
    """

    max_concurrent_api_calls: int = 5
    base_branch: str = "main"
    worktree_retention_days: int = 7
    max_pipeline_depth: int = 20
    artifact_exclude_patterns: list[str] = field(default_factory=lambda: [
        "*.env", "credentials*", "*.key", "*.pem", "*.secret",
    ])


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
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)


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

    # Parse execution config
    exec_raw = data.get("execution", {}) or {}
    default_excludes = ["*.env", "credentials*", "*.key", "*.pem", "*.secret"]
    execution = ExecutionConfig(
        max_concurrent_api_calls=exec_raw.get("max_concurrent_api_calls", 5),
        base_branch=exec_raw.get("base_branch", "main"),
        worktree_retention_days=exec_raw.get("worktree_retention_days", 7),
        max_pipeline_depth=exec_raw.get("max_pipeline_depth", 20),
        artifact_exclude_patterns=exec_raw.get(
            "artifact_exclude_patterns", default_excludes
        ),
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
        execution=execution,
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


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------


@dataclass
class PipelineEdge:
    """A single directed edge in the pipeline graph."""

    id: str
    from_agent: str
    to_agent: str
    task_types: list[str] = field(default_factory=list)
    on_failure: str = "block"  # "block", "continue_partial", "cancel_pipeline"


@dataclass
class PipelineNodeConfig:
    """Per-node configuration in the pipeline (receiving-side settings)."""

    join_strategy: str = "wait_all"  # "wait_all" or "stream"


@dataclass
class PipelineConfig:
    """Top-level pipeline topology stored in team.yaml."""

    id: str = "default-pipeline"
    name: str = "Default Pipeline"
    start_agent: str | None = None
    edges: list[PipelineEdge] = field(default_factory=list)
    node_config: dict[str, PipelineNodeConfig] = field(default_factory=dict)


def load_pipeline(team_yaml_path: Path) -> PipelineConfig:
    """Load pipeline topology from team.yaml.

    Parameters
    ----------
    team_yaml_path:
        Path to the team YAML file (e.g. ``config/team.yaml``).

    Returns
    -------
    PipelineConfig
        Parsed pipeline configuration. Returns a default empty pipeline
        if the ``pipeline`` key is missing from the YAML.

    Raises
    ------
    FileNotFoundError
        If *team_yaml_path* does not exist.
    """
    if not team_yaml_path.exists():
        raise FileNotFoundError(f"Team config not found: {team_yaml_path}")

    with open(team_yaml_path) as f:
        data = yaml.safe_load(f) or {}

    pipeline_raw = data.get("pipeline")
    if not pipeline_raw:
        return PipelineConfig()

    edges = []
    for e in pipeline_raw.get("edges", []):
        edges.append(PipelineEdge(
            id=e["id"],
            from_agent=e["from"],
            to_agent=e["to"],
            task_types=e.get("task_types", []),
            on_failure=e.get("on_failure", "block"),
        ))

    node_config: dict[str, PipelineNodeConfig] = {}
    for role_name, nc_raw in pipeline_raw.get("node_config", {}).items():
        node_config[role_name] = PipelineNodeConfig(
            join_strategy=nc_raw.get("join_strategy", "wait_all"),
        )

    return PipelineConfig(
        id=pipeline_raw.get("id", "default-pipeline"),
        name=pipeline_raw.get("name", "Default Pipeline"),
        start_agent=pipeline_raw.get("start_agent"),
        edges=edges,
        node_config=node_config,
    )


def save_pipeline(team_yaml_path: Path, pipeline: PipelineConfig) -> None:
    """Persist pipeline topology to team.yaml.

    Reads the existing file, updates the ``pipeline`` key, and writes back.
    All other top-level keys are preserved.

    Parameters
    ----------
    team_yaml_path:
        Path to the team YAML file.
    pipeline:
        The pipeline config to save.
    """
    with open(team_yaml_path) as f:
        data = yaml.safe_load(f) or {}

    data["pipeline"] = {
        "id": pipeline.id,
        "name": pipeline.name,
        "start_agent": pipeline.start_agent,
        "edges": [
            {
                "id": e.id,
                "from": e.from_agent,
                "to": e.to_agent,
                "task_types": e.task_types,
                "on_failure": e.on_failure,
            }
            for e in pipeline.edges
        ],
        "node_config": {
            role: {"join_strategy": nc.join_strategy}
            for role, nc in pipeline.node_config.items()
        },
    }

    with open(team_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def migrate_routes_to_pipeline(roles: dict[str, RoleConfig]) -> PipelineConfig:
    """Auto-generate a PipelineConfig from per-role routes_to fields.

    Used on first load when team.yaml has no ``pipeline`` section but
    roles have ``routes_to`` entries.

    Parameters
    ----------
    roles:
        Mapping of role name to RoleConfig (as returned by :func:`load_roles`).

    Returns
    -------
    PipelineConfig
        A new pipeline with edges derived from all roles' ``routes_to``.
    """
    edges: list[PipelineEdge] = []
    edge_counter = 0

    for role_name, rc in roles.items():
        for rt in rc.routes_to:
            # Skip routes to non-existent roles
            if rt.role not in roles:
                logger.warning(
                    "Migration: skipping route from '%s' to unknown role '%s'",
                    role_name, rt.role,
                )
                continue
            edge_counter += 1
            edges.append(PipelineEdge(
                id=f"migrated-edge-{edge_counter}",
                from_agent=role_name,
                to_agent=rt.role,
                task_types=rt.task_types,
                on_failure="block",
            ))

    # Detect start agent: role with no inbound edges (and at least one
    # outbound edge, or at least one edge exists).
    if edges:
        all_roles = set(roles.keys())
        routed_to = {e.to_agent for e in edges}
        entry_points = all_roles - routed_to
        # Among entry points, prefer those with outbound edges
        entry_with_outbound = [
            r for r in entry_points
            if any(e.from_agent == r for e in edges)
        ]
        start_agent = entry_with_outbound[0] if entry_with_outbound else None
    else:
        start_agent = None

    return PipelineConfig(
        id="default-pipeline",
        name="Default Pipeline",
        start_agent=start_agent,
        edges=edges,
        node_config={},
    )


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
    # --- New fields (v2) ---
    approval_mode: str = "auto"  # "auto", "manual", "first_run"
    max_revision_cycles: int = 0  # 0 = unlimited
    max_clarification_requests: int = 10
    max_route_tasks: int = 100
    uses_worktree: bool = False
    capabilities: list[str] = field(default_factory=list)
    artifact_exclude_patterns: list[str] = field(default_factory=list)


def _parse_role(data: dict) -> RoleConfig:
    """Parse a single role YAML dict into a RoleConfig."""

    # Fix 1: Validate required role keys
    for key in _REQUIRED_ROLE_KEYS:
        if key not in data:
            raise ValueError(f"Role config missing required key '{key}' (file may be incomplete)")

    approval_mode = data.get("approval_mode", "auto")
    if approval_mode not in ("auto", "manual", "first_run"):
        raise ValueError(
            f"approval_mode must be 'auto', 'manual', or 'first_run', got '{approval_mode}'"
        )

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
        approval_mode=approval_mode,
        max_revision_cycles=data.get("max_revision_cycles", 0),
        max_clarification_requests=data.get("max_clarification_requests", 10),
        max_route_tasks=data.get("max_route_tasks", 100),
        uses_worktree=data.get("uses_worktree", False),
        capabilities=data.get("capabilities", []),
        artifact_exclude_patterns=data.get("artifact_exclude_patterns", []),
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


def load_presets(presets_dir: Path) -> dict[str, dict]:
    """Load preset YAML files from directory. Returns raw dicts keyed by preset_id."""
    if not presets_dir.is_dir():
        return {}
    presets: dict[str, dict] = {}
    for yaml_file in sorted(presets_dir.glob("*.yaml")):
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            logger.warning("Skipping invalid preset file %s: %s", yaml_file.name, exc)
            continue
        if not data or "preset_id" not in data:
            continue
        presets[data["preset_id"]] = data
    return presets


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
