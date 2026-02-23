# tests/test_agent_roles.py
import pytest
from taskbrew.agents.roles import get_agent_config, AGENT_ROLES


def test_all_roles_defined():
    expected = {"pm", "researcher", "architect", "coder", "tester", "reviewer"}
    assert set(AGENT_ROLES.keys()) == expected


def test_get_agent_config_coder():
    config = get_agent_config("coder")
    assert config.name == "coder"
    assert "Write" in config.allowed_tools
    assert "Edit" in config.allowed_tools
    assert "Bash" in config.allowed_tools


def test_get_agent_config_reviewer():
    config = get_agent_config("reviewer")
    assert config.name == "reviewer"
    assert "Write" not in config.allowed_tools
    assert "Read" in config.allowed_tools


def test_get_agent_config_unknown_raises():
    with pytest.raises(KeyError):
        get_agent_config("nonexistent")


def test_each_role_has_system_prompt():
    for name in AGENT_ROLES:
        config = get_agent_config(name)
        assert len(config.system_prompt) > 50, f"{name} system prompt too short"
