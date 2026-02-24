"""Tests for ai_team.config_loader — team config, role loading, and routing validation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from ai_team.config_loader import (
    AutoScaleConfig,
    AutoScaleDefaults,
    RoleConfig,
    RouteTarget,
    TeamConfig,
    load_roles,
    load_team_config,
    validate_routing,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEAM_YAML = dedent("""\
    team_name: "Test Team"

    database:
      path: "data/test.db"

    dashboard:
      host: "0.0.0.0"
      port: 9000

    artifacts:
      base_dir: "out"

    defaults:
      max_instances: 2
      poll_interval_seconds: 10
      idle_timeout_minutes: 60
      auto_scale:
        enabled: true
        scale_up_threshold: 5
        scale_down_idle: 20

    approval_required: [prd]

    group_prefixes:
      pm: "FEAT"
""")


PM_YAML = dedent("""\
    role: pm
    display_name: "Product Manager"
    prefix: "PM"
    color: "#3b82f6"
    emoji: "clip"

    system_prompt: "You are a PM."

    tools: [Read, Glob]
    produces: [prd]
    accepts: [goal]
    routes_to:
      - role: architect
        task_types: [tech_design]
    can_create_groups: true
    group_type: "FEAT"
    max_instances: 1
    requires_approval: [prd]
    context_includes:
      - parent_artifact
""")

ARCHITECT_YAML = dedent("""\
    role: architect
    display_name: "Architect"
    prefix: "AR"
    color: "#8b5cf6"
    emoji: "build"

    system_prompt: "You are an architect."

    tools: [Read, Write]
    produces: [tech_design]
    accepts: [prd]
    routes_to:
      - role: coder
        task_types: [implementation]
    can_create_groups: false
    max_instances: 2
    auto_scale:
      enabled: true
      scale_up_threshold: 4
      scale_down_idle: 20
    requires_approval: []
    context_includes: []
""")

CODER_YAML = dedent("""\
    role: coder
    display_name: "Coder"
    prefix: "CD"
    color: "#f59e0b"
    emoji: "laptop"

    system_prompt: "You are a coder."

    tools: [Read, Write, Bash]
    produces: [implementation]
    accepts: [implementation]
    routes_to: []
    can_create_groups: false
    max_instances: 3
    requires_approval: []
    context_includes: []
""")


# ---------------------------------------------------------------------------
# Task 1 — load_team_config
# ---------------------------------------------------------------------------


class TestLoadTeamConfig:
    """Tests for load_team_config()."""

    def test_load_valid_team_config(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(TEAM_YAML)

        cfg = load_team_config(cfg_file)

        assert isinstance(cfg, TeamConfig)
        assert cfg.team_name == "Test Team"
        assert cfg.db_path == "data/test.db"
        assert cfg.dashboard_host == "0.0.0.0"
        assert cfg.dashboard_port == 9000
        assert cfg.artifacts_base_dir == "out"
        assert cfg.default_max_instances == 2
        assert cfg.default_poll_interval == 10
        assert cfg.default_idle_timeout == 60

        # Auto-scale defaults
        assert isinstance(cfg.default_auto_scale, AutoScaleDefaults)
        assert cfg.default_auto_scale.enabled is True
        assert cfg.default_auto_scale.scale_up_threshold == 5
        assert cfg.default_auto_scale.scale_down_idle == 20

        assert cfg.approval_required == ["prd"]
        assert cfg.group_prefixes == {"pm": "FEAT"}

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.yaml"
        with pytest.raises(FileNotFoundError, match="Team config not found"):
            load_team_config(missing)


# ---------------------------------------------------------------------------
# Task 2 — load_roles
# ---------------------------------------------------------------------------


class TestLoadRoles:
    """Tests for load_roles()."""

    def test_load_single_role(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "pm.yaml").write_text(PM_YAML)

        roles = load_roles(roles_dir)

        assert len(roles) == 1
        assert "pm" in roles

        pm = roles["pm"]
        assert isinstance(pm, RoleConfig)
        assert pm.display_name == "Product Manager"
        assert pm.prefix == "PM"
        assert pm.color == "#3b82f6"
        assert pm.system_prompt == "You are a PM."
        assert pm.tools == ["Read", "Glob"]
        assert pm.produces == ["prd"]
        assert pm.accepts == ["goal"]
        assert len(pm.routes_to) == 1
        assert pm.routes_to[0].role == "architect"
        assert pm.routes_to[0].task_types == ["tech_design"]
        assert pm.can_create_groups is True
        assert pm.group_type == "FEAT"
        assert pm.max_instances == 1
        assert pm.auto_scale is None
        assert pm.requires_approval == ["prd"]
        assert pm.context_includes == ["parent_artifact"]

    def test_load_role_with_auto_scale(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "architect.yaml").write_text(ARCHITECT_YAML)

        roles = load_roles(roles_dir)
        arch = roles["architect"]

        assert isinstance(arch.auto_scale, AutoScaleConfig)
        assert arch.auto_scale.enabled is True
        assert arch.auto_scale.scale_up_threshold == 4
        assert arch.auto_scale.scale_down_idle == 20

    def test_load_empty_directory(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()

        roles = load_roles(roles_dir)

        assert roles == {}

    def test_load_nonexistent_directory(self, tmp_path: Path) -> None:
        roles = load_roles(tmp_path / "nonexistent")
        assert roles == {}

    def test_load_multiple_roles(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "pm.yaml").write_text(PM_YAML)
        (roles_dir / "architect.yaml").write_text(ARCHITECT_YAML)
        (roles_dir / "coder.yaml").write_text(CODER_YAML)

        roles = load_roles(roles_dir)

        assert len(roles) == 3
        assert set(roles.keys()) == {"pm", "architect", "coder"}


# ---------------------------------------------------------------------------
# Task 3 — validate_routing
# ---------------------------------------------------------------------------


class TestValidateRouting:
    """Tests for validate_routing()."""

    def _make_roles(self, *yamls: str, tmp_path: Path) -> dict[str, RoleConfig]:
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir(exist_ok=True)
        for i, y in enumerate(yamls):
            (roles_dir / f"role_{i}.yaml").write_text(y)
        return load_roles(roles_dir)

    def test_valid_routing(self, tmp_path: Path) -> None:
        roles = self._make_roles(PM_YAML, ARCHITECT_YAML, CODER_YAML, tmp_path=tmp_path)
        errors = validate_routing(roles)
        assert errors == []

    def test_missing_target(self, tmp_path: Path) -> None:
        """PM routes to architect, but architect is not loaded."""
        roles = self._make_roles(PM_YAML, tmp_path=tmp_path)
        errors = validate_routing(roles)
        assert len(errors) == 1
        assert "unknown role 'architect'" in errors[0]

    def test_no_entry_point(self, tmp_path: Path) -> None:
        """Every role is a route target -> no entry point."""
        role_a = dedent("""\
            role: alpha
            display_name: "Alpha"
            prefix: "AL"
            color: "#000"
            emoji: "a"
            system_prompt: "alpha"
            routes_to:
              - role: beta
                task_types: [x]
        """)
        role_b = dedent("""\
            role: beta
            display_name: "Beta"
            prefix: "BE"
            color: "#111"
            emoji: "b"
            system_prompt: "beta"
            routes_to:
              - role: alpha
                task_types: [y]
        """)
        roles = self._make_roles(role_a, role_b, tmp_path=tmp_path)
        errors = validate_routing(roles)
        assert any("No entry-point role found" in e for e in errors)

    def test_duplicate_prefix(self, tmp_path: Path) -> None:
        """Two roles with the same prefix should be flagged."""
        role_a = dedent("""\
            role: first
            display_name: "First"
            prefix: "DUP"
            color: "#000"
            emoji: "a"
            system_prompt: "first"
            routes_to: []
        """)
        role_b = dedent("""\
            role: second
            display_name: "Second"
            prefix: "DUP"
            color: "#111"
            emoji: "b"
            system_prompt: "second"
            routes_to: []
        """)
        roles = self._make_roles(role_a, role_b, tmp_path=tmp_path)
        errors = validate_routing(roles)
        assert any("Duplicate prefix 'DUP'" in e for e in errors)

    def test_empty_roles_is_valid(self) -> None:
        """An empty roles dict should produce no errors."""
        errors = validate_routing({})
        assert errors == []
