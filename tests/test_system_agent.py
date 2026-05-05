"""Tests for the per-project TaskBrew system agent profile."""

from pathlib import Path
from types import SimpleNamespace

from taskbrew.system_agent import SYSTEM_AGENT_PROMPT, build_system_agent_config


def _team_config(system_agent: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        team_name="System Agent Team",
        db_path="data/system.db",
        dashboard_host="0.0.0.0",
        dashboard_port=8420,
        artifacts_base_dir="artifacts",
        default_max_instances=1,
        default_poll_interval=5,
        default_idle_timeout=30,
        cli_provider="claude",
        system_agent=system_agent,
        mcp_servers={"task-tools": object()},
    )


def test_build_system_agent_config_uses_project_profile(tmp_path: Path) -> None:
    team_config = _team_config(
        SimpleNamespace(
            provider="codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
        )
    )

    config = build_system_agent_config(team_config, project_dir=tmp_path)

    assert config.name == "system"
    assert config.role == "TaskBrew System Admin"
    assert config.system_prompt == SYSTEM_AGENT_PROMPT
    assert config.cli_provider == "codex"
    assert config.model == "gpt-5.4-mini"
    assert config.reasoning_effort == "low"
    assert config.cwd == tmp_path
    assert config.mcp_servers == team_config.mcp_servers


def test_system_agent_prompt_is_admin_owned() -> None:
    assert "TaskBrew system administrator" in SYSTEM_AGENT_PROMPT
    assert "team role" in SYSTEM_AGENT_PROMPT
