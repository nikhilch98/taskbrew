"""Edge-case tests for config loading and validation (TEST-005).

Covers: missing required fields, invalid models, invalid routing,
empty tools, circular routing, bad max_turns / max_instances,
special characters in role names, and team config edge cases.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from taskbrew.config_loader import (
    RoleConfig,
    RouteTarget,
    _parse_role,
    load_roles,
    load_team_config,
    validate_routing,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_team_yaml(**overrides: object) -> str:
    """Return a valid team YAML string, with optional top-level overrides."""
    base: dict = {
        "team_name": "Edge Team",
        "database": {"path": "data/edge.db"},
        "dashboard": {"host": "127.0.0.1", "port": 8000},
        "artifacts": {"base_dir": "art"},
    }
    base.update(overrides)
    return yaml.dump(base, default_flow_style=False)


def _minimal_role_yaml(**overrides: object) -> str:
    """Return a valid minimal role YAML string with optional field overrides."""
    base: dict = {
        "role": "tester",
        "display_name": "Tester",
        "prefix": "TS",
        "color": "#00ff00",
        "emoji": "check",
        "system_prompt": "You are a tester.",
    }
    base.update(overrides)
    return yaml.dump(base, default_flow_style=False)


def _write_role(roles_dir: Path, filename: str, content: str) -> None:
    roles_dir.mkdir(exist_ok=True)
    (roles_dir / filename).write_text(content)


# ---------------------------------------------------------------------------
# 1. Loading a valid team config (defaults, optional sections)
# ---------------------------------------------------------------------------


class TestTeamConfigValid:
    """Verify team config loads correctly including optional / default values."""

    def test_minimal_team_config_defaults(self, tmp_path: Path) -> None:
        """A team YAML with only required keys should populate defaults."""
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(_minimal_team_yaml())

        cfg = load_team_config(cfg_file)

        assert cfg.team_name == "Edge Team"
        assert cfg.db_path == "data/edge.db"
        assert cfg.dashboard_host == "127.0.0.1"
        assert cfg.dashboard_port == 8000
        assert cfg.artifacts_base_dir == "art"
        # defaults section absent -> uses dataclass / code defaults
        assert cfg.default_max_instances == 1
        assert cfg.default_poll_interval == 5
        assert cfg.default_idle_timeout == 30
        assert cfg.default_auto_scale.enabled is False
        assert cfg.group_prefixes == {}
        assert cfg.auth_enabled is False
        assert cfg.auth_tokens == []
        assert cfg.cost_budgets_enabled is False
        assert cfg.webhooks_enabled is False

    def test_team_config_with_all_sections(self, tmp_path: Path) -> None:
        """A fully-specified team YAML should parse every section."""
        full_yaml = dedent("""\
            team_name: "Full Team"
            database:
              path: "data/full.db"
            dashboard:
              host: "0.0.0.0"
              port: 9999
            artifacts:
              base_dir: "artifacts"
            defaults:
              max_instances: 5
              poll_interval_seconds: 2
              idle_timeout_minutes: 120
              auto_scale:
                enabled: true
                scale_up_threshold: 10
                scale_down_idle: 30
            group_prefixes:
              pm: "FEAT"
              arch: "DEBT"
            auth:
              enabled: true
              tokens: ["tok1", "tok2"]
            cost_budgets:
              enabled: true
            webhooks:
              enabled: true
        """)
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(full_yaml)

        cfg = load_team_config(cfg_file)

        assert cfg.team_name == "Full Team"
        assert cfg.default_max_instances == 5
        assert cfg.default_poll_interval == 2
        assert cfg.default_idle_timeout == 120
        assert cfg.default_auto_scale.enabled is True
        assert cfg.default_auto_scale.scale_up_threshold == 10
        assert cfg.default_auto_scale.scale_down_idle == 30
        assert cfg.group_prefixes == {"pm": "FEAT", "arch": "DEBT"}
        assert cfg.auth_enabled is True
        assert cfg.auth_tokens == ["tok1", "tok2"]
        assert cfg.cost_budgets_enabled is True
        assert cfg.webhooks_enabled is True


# ---------------------------------------------------------------------------
# 2. Loading valid role configs
# ---------------------------------------------------------------------------


class TestRoleConfigValid:
    """Verify roles load with correct field values and defaults."""

    def test_minimal_role_gets_defaults(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        _write_role(roles_dir, "tester.yaml", _minimal_role_yaml())

        roles = load_roles(roles_dir)

        assert "tester" in roles
        r = roles["tester"]
        assert r.role == "tester"
        assert r.display_name == "Tester"
        assert r.model == "claude-opus-4-6"  # default
        assert r.tools == []
        assert r.produces == []
        assert r.accepts == []
        assert r.routes_to == []
        assert r.can_create_groups is False
        assert r.group_type is None
        assert r.max_instances == 1
        assert r.auto_scale is None
        assert r.context_includes == []
        assert r.max_execution_time == 1800

    def test_role_with_explicit_model(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        _write_role(
            roles_dir,
            "custom.yaml",
            _minimal_role_yaml(role="custom", prefix="CU", model="claude-sonnet-4-6"),
        )

        roles = load_roles(roles_dir)
        assert roles["custom"].model == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 3. Missing required fields
# ---------------------------------------------------------------------------


class TestMissingRequiredFields:
    """Required role fields: role, display_name, prefix, color, emoji, system_prompt."""

    @pytest.mark.parametrize(
        "missing_field",
        ["role", "display_name", "prefix", "color", "emoji", "system_prompt"],
    )
    def test_missing_required_role_field_raises(
        self, missing_field: str, tmp_path: Path
    ) -> None:
        base = {
            "role": "x",
            "display_name": "X",
            "prefix": "XX",
            "color": "#000",
            "emoji": "e",
            "system_prompt": "prompt",
        }
        del base[missing_field]

        with pytest.raises((KeyError, ValueError), match=missing_field):
            _parse_role(base)

    def test_missing_team_name_raises(self, tmp_path: Path) -> None:
        bad_yaml = dedent("""\
            database:
              path: "data/test.db"
            dashboard:
              host: "0.0.0.0"
              port: 8000
            artifacts:
              base_dir: "art"
        """)
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(bad_yaml)

        with pytest.raises((KeyError, ValueError), match="team_name"):
            load_team_config(cfg_file)

    def test_missing_database_section_raises(self, tmp_path: Path) -> None:
        bad_yaml = dedent("""\
            team_name: "No DB"
            dashboard:
              host: "0.0.0.0"
              port: 8000
            artifacts:
              base_dir: "art"
        """)
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(bad_yaml)

        with pytest.raises((KeyError, TypeError, ValueError)):
            load_team_config(cfg_file)

    def test_missing_dashboard_section_raises(self, tmp_path: Path) -> None:
        bad_yaml = dedent("""\
            team_name: "No Dash"
            database:
              path: "data/test.db"
            artifacts:
              base_dir: "art"
        """)
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(bad_yaml)

        with pytest.raises((KeyError, TypeError, ValueError)):
            load_team_config(cfg_file)

    def test_missing_artifacts_section_raises(self, tmp_path: Path) -> None:
        bad_yaml = dedent("""\
            team_name: "No Artifacts"
            database:
              path: "data/test.db"
            dashboard:
              host: "0.0.0.0"
              port: 8000
        """)
        cfg_file = tmp_path / "team.yaml"
        cfg_file.write_text(bad_yaml)

        with pytest.raises((KeyError, TypeError, ValueError)):
            load_team_config(cfg_file)


# ---------------------------------------------------------------------------
# 4. Invalid model names
# ---------------------------------------------------------------------------


class TestInvalidModelNames:
    """The loader does not validate model names — verify it stores whatever is given."""

    def test_nonsense_model_stored_as_is(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        _write_role(
            roles_dir,
            "bad.yaml",
            _minimal_role_yaml(role="bad", prefix="BD", model="not-a-real-model"),
        )

        roles = load_roles(roles_dir)
        assert roles["bad"].model == "not-a-real-model"

    def test_empty_string_model(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        _write_role(
            roles_dir,
            "empty.yaml",
            _minimal_role_yaml(role="empty", prefix="EM", model=""),
        )

        roles = load_roles(roles_dir)
        assert roles["empty"].model == ""

    def test_model_omitted_uses_default(self, tmp_path: Path) -> None:
        """When 'model' key is absent the default is applied."""
        roles_dir = tmp_path / "roles"
        _write_role(roles_dir, "nomod.yaml", _minimal_role_yaml(role="nomod", prefix="NM"))

        roles = load_roles(roles_dir)
        assert roles["nomod"].model == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# 5. Invalid routing (routes_to referencing non-existent roles)
# ---------------------------------------------------------------------------


class TestInvalidRouting:
    """validate_routing should flag bad route targets."""

    def test_route_to_nonexistent_role(self) -> None:
        roles = {
            "alpha": RoleConfig(
                role="alpha",
                display_name="Alpha",
                prefix="AL",
                color="#000",
                emoji="a",
                system_prompt="alpha",
                routes_to=[RouteTarget(role="ghost", task_types=["x"])],
            )
        }
        errors = validate_routing(roles)
        assert any("unknown role 'ghost'" in e for e in errors)

    def test_multiple_bad_targets(self) -> None:
        roles = {
            "alpha": RoleConfig(
                role="alpha",
                display_name="Alpha",
                prefix="AL",
                color="#000",
                emoji="a",
                system_prompt="alpha",
                routes_to=[
                    RouteTarget(role="nope1", task_types=[]),
                    RouteTarget(role="nope2", task_types=[]),
                ],
            )
        }
        errors = validate_routing(roles)
        assert sum("unknown role" in e for e in errors) == 2


# ---------------------------------------------------------------------------
# 6. Empty tools list
# ---------------------------------------------------------------------------


class TestEmptyToolsList:
    """tools: [] and tools omitted should both result in an empty list."""

    def test_explicit_empty_tools(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        _write_role(
            roles_dir,
            "notool.yaml",
            _minimal_role_yaml(role="notool", prefix="NT", tools=[]),
        )

        roles = load_roles(roles_dir)
        assert roles["notool"].tools == []

    def test_tools_key_omitted(self, tmp_path: Path) -> None:
        """When 'tools' is absent from YAML, default is an empty list."""
        base = {
            "role": "bare",
            "display_name": "Bare",
            "prefix": "BA",
            "color": "#111",
            "emoji": "b",
            "system_prompt": "bare",
        }
        # no 'tools' key at all
        role = _parse_role(base)
        assert role.tools == []


# ---------------------------------------------------------------------------
# 7. Circular routing (A -> B -> A)
# ---------------------------------------------------------------------------


class TestCircularRouting:
    """Circular routes cause every role to be a route target -> no entry point."""

    def test_two_role_cycle(self) -> None:
        roles = {
            "alpha": RoleConfig(
                role="alpha",
                display_name="Alpha",
                prefix="AL",
                color="#000",
                emoji="a",
                system_prompt="alpha",
                routes_to=[RouteTarget(role="beta", task_types=["x"])],
            ),
            "beta": RoleConfig(
                role="beta",
                display_name="Beta",
                prefix="BE",
                color="#111",
                emoji="b",
                system_prompt="beta",
                routes_to=[RouteTarget(role="alpha", task_types=["y"])],
            ),
        }
        errors = validate_routing(roles)
        assert any("No entry-point role found" in e for e in errors)

    def test_three_role_cycle(self) -> None:
        roles = {
            "a": RoleConfig(
                role="a", display_name="A", prefix="AA", color="#000",
                emoji="a", system_prompt="a",
                routes_to=[RouteTarget(role="b", task_types=[])],
            ),
            "b": RoleConfig(
                role="b", display_name="B", prefix="BB", color="#111",
                emoji="b", system_prompt="b",
                routes_to=[RouteTarget(role="c", task_types=[])],
            ),
            "c": RoleConfig(
                role="c", display_name="C", prefix="CC", color="#222",
                emoji="c", system_prompt="c",
                routes_to=[RouteTarget(role="a", task_types=[])],
            ),
        }
        errors = validate_routing(roles)
        assert any("No entry-point role found" in e for e in errors)

    def test_self_referencing_route(self) -> None:
        """A role that routes to itself is still a route target."""
        roles = {
            "solo": RoleConfig(
                role="solo", display_name="Solo", prefix="SO", color="#000",
                emoji="s", system_prompt="solo",
                routes_to=[RouteTarget(role="solo", task_types=[])],
            ),
        }
        errors = validate_routing(roles)
        assert any("No entry-point role found" in e for e in errors)


# ---------------------------------------------------------------------------
# 8. max_turns = 0 or negative
# ---------------------------------------------------------------------------


class TestMaxTurnsEdgeCases:
    """max_turns is not parsed by config_loader (it is agent-level), but
    max_execution_time is.  Verify zero / negative values are stored as-is
    (the loader does not validate numeric ranges)."""

    def test_max_execution_time_zero(self) -> None:
        with pytest.raises(ValueError, match="max_execution_time"):
            _parse_role({
                "role": "z", "display_name": "Z", "prefix": "ZZ",
                "color": "#000", "emoji": "z", "system_prompt": "z",
                "max_execution_time": 0,
            })

    def test_max_execution_time_negative(self) -> None:
        with pytest.raises(ValueError, match="max_execution_time"):
            _parse_role({
                "role": "n", "display_name": "N", "prefix": "NN",
                "color": "#000", "emoji": "n", "system_prompt": "n",
                "max_execution_time": -5,
            })


# ---------------------------------------------------------------------------
# 9. Invalid max_instances
# ---------------------------------------------------------------------------


class TestInvalidMaxInstances:
    """max_instances is stored as-is by the loader (no range validation)."""

    def test_max_instances_zero(self) -> None:
        role = _parse_role({
            "role": "mi0", "display_name": "MI0", "prefix": "M0",
            "color": "#000", "emoji": "m", "system_prompt": "m",
            "max_instances": 0,
        })
        assert role.max_instances == 0

    def test_max_instances_negative(self) -> None:
        role = _parse_role({
            "role": "min", "display_name": "MIN", "prefix": "MN",
            "color": "#000", "emoji": "m", "system_prompt": "m",
            "max_instances": -1,
        })
        assert role.max_instances == -1

    def test_max_instances_very_large(self) -> None:
        role = _parse_role({
            "role": "big", "display_name": "BIG", "prefix": "BG",
            "color": "#000", "emoji": "b", "system_prompt": "b",
            "max_instances": 999999,
        })
        assert role.max_instances == 999999

    def test_max_instances_default_when_omitted(self) -> None:
        role = _parse_role({
            "role": "def", "display_name": "DEF", "prefix": "DF",
            "color": "#000", "emoji": "d", "system_prompt": "d",
        })
        assert role.max_instances == 1


# ---------------------------------------------------------------------------
# 10. Special characters in role names
# ---------------------------------------------------------------------------


class TestSpecialCharacterRoleNames:
    """Role names with special characters should load without error."""

    @pytest.mark.parametrize(
        "role_name",
        [
            "role-with-dashes",
            "role_with_underscores",
            "role.with.dots",
            "UPPERCASE",
            "MixedCase123",
            "role with spaces",
        ],
    )
    def test_special_char_role_names_load(self, role_name: str) -> None:
        role = _parse_role({
            "role": role_name,
            "display_name": role_name,
            "prefix": "SP",
            "color": "#000",
            "emoji": "s",
            "system_prompt": "special",
        })
        assert role.role == role_name

    def test_special_char_role_name_via_yaml_file(self, tmp_path: Path) -> None:
        roles_dir = tmp_path / "roles"
        _write_role(
            roles_dir,
            "special.yaml",
            _minimal_role_yaml(role="my-special_role.v2", prefix="SP"),
        )

        roles = load_roles(roles_dir)
        assert "my-special_role.v2" in roles

    def test_unicode_role_name(self) -> None:
        role = _parse_role({
            "role": "rolle_\u00e4\u00f6\u00fc",
            "display_name": "Unicode Role",
            "prefix": "UN",
            "color": "#000",
            "emoji": "u",
            "system_prompt": "unicode",
        })
        assert role.role == "rolle_\u00e4\u00f6\u00fc"


# ---------------------------------------------------------------------------
# 11. Additional edge cases
# ---------------------------------------------------------------------------


class TestAdditionalEdgeCases:
    """Miscellaneous edge cases not covered above."""

    def test_empty_yaml_file_skipped(self, tmp_path: Path) -> None:
        """An empty YAML file should not crash load_roles."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "empty.yaml").write_text("")

        roles = load_roles(roles_dir)
        assert roles == {}

    def test_yaml_with_only_comments_skipped(self, tmp_path: Path) -> None:
        """A YAML file with only comments parses as None and is skipped."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "comments.yaml").write_text("# just a comment\n# nothing here\n")

        roles = load_roles(roles_dir)
        assert roles == {}

    def test_non_yaml_files_ignored(self, tmp_path: Path) -> None:
        """Files without .yaml extension should be ignored."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "readme.txt").write_text("not a role")
        (roles_dir / "data.json").write_text("{}")

        roles = load_roles(roles_dir)
        assert roles == {}

    def test_duplicate_role_name_last_wins(self, tmp_path: Path) -> None:
        """If two YAML files define the same role name, the last file (sorted) wins."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "a_first.yaml").write_text(
            _minimal_role_yaml(role="dup", prefix="D1", display_name="First")
        )
        (roles_dir / "b_second.yaml").write_text(
            _minimal_role_yaml(role="dup", prefix="D2", display_name="Second")
        )

        roles = load_roles(roles_dir)
        assert len(roles) == 1
        assert roles["dup"].display_name == "Second"
        assert roles["dup"].prefix == "D2"

    def test_routes_to_with_empty_task_types(self) -> None:
        """routes_to entry with no task_types should default to []."""
        data = {
            "role": "r",
            "display_name": "R",
            "prefix": "RR",
            "color": "#000",
            "emoji": "r",
            "system_prompt": "r",
            "routes_to": [{"role": "other"}],
        }
        role = _parse_role(data)
        assert role.routes_to[0].task_types == []

    def test_auto_scale_partial_config(self) -> None:
        """auto_scale with only 'enabled' should fill other fields with defaults."""
        data = {
            "role": "as",
            "display_name": "AS",
            "prefix": "AS",
            "color": "#000",
            "emoji": "a",
            "system_prompt": "as",
            "auto_scale": {"enabled": True},
        }
        role = _parse_role(data)
        assert role.auto_scale is not None
        assert role.auto_scale.enabled is True
        assert role.auto_scale.scale_up_threshold == 3  # default
        assert role.auto_scale.scale_down_idle == 15  # default

    def test_team_config_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Team config not found"):
            load_team_config(tmp_path / "nonexistent.yaml")

    def test_validate_routing_duplicate_prefix_detection(self) -> None:
        """Directly construct roles with duplicate prefixes and validate."""
        roles = {
            "one": RoleConfig(
                role="one", display_name="One", prefix="SAME",
                color="#000", emoji="1", system_prompt="one",
            ),
            "two": RoleConfig(
                role="two", display_name="Two", prefix="SAME",
                color="#111", emoji="2", system_prompt="two",
            ),
        }
        errors = validate_routing(roles)
        assert any("Duplicate prefix 'SAME'" in e for e in errors)

    def test_valid_routing_with_terminal_role(self) -> None:
        """A linear chain with a terminal role (no outbound routes) is valid."""
        roles = {
            "entry": RoleConfig(
                role="entry", display_name="Entry", prefix="EN",
                color="#000", emoji="e", system_prompt="entry",
                routes_to=[RouteTarget(role="terminal", task_types=[])],
            ),
            "terminal": RoleConfig(
                role="terminal", display_name="Terminal", prefix="TE",
                color="#111", emoji="t", system_prompt="terminal",
            ),
        }
        errors = validate_routing(roles)
        assert errors == []
