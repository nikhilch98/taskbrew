# tests/test_agent_base.py
import pytest
from ai_team.agents.base import AgentRunner, AgentStatus
from ai_team.config import AgentConfig


def test_agent_config_creation():
    config = AgentConfig(
        name="coder",
        role="Implements code",
        system_prompt="You are a coder agent.",
        allowed_tools=["Read", "Write", "Edit", "Bash"],
    )
    assert config.name == "coder"
    assert "Bash" in config.allowed_tools


def test_agent_runner_initialization():
    config = AgentConfig(
        name="reviewer",
        role="Reviews code",
        system_prompt="You are a reviewer.",
        allowed_tools=["Read", "Glob", "Grep"],
    )
    runner = AgentRunner(config)
    assert runner.name == "reviewer"
    assert runner.status == AgentStatus.IDLE


def test_agent_runner_builds_options():
    config = AgentConfig(
        name="coder",
        role="Coder",
        system_prompt="You are a coding agent.",
        allowed_tools=["Read", "Write", "Bash"],
    )
    runner = AgentRunner(config)
    options = runner.build_options()
    assert options.system_prompt == "You are a coding agent."
    assert "Read" in options.allowed_tools
    assert options.permission_mode == "bypassPermissions"
