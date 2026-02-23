"""Tests for config-driven MCP server registration."""
from __future__ import annotations
import os
from taskbrew.config_loader import MCPServerConfig


def test_build_mcp_dict_builtin():
    """Built-in MCP servers should use sys.executable with module args."""
    from taskbrew.agents.provider import _build_mcp_dict
    servers = {
        "task-tools": MCPServerConfig(builtin=True),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert "task-tools" in result
    assert result["task-tools"]["type"] == "stdio"
    assert "-m" in result["task-tools"]["args"]
    assert "taskbrew.tools.task_tools" in result["task-tools"]["args"]


def test_build_mcp_dict_custom():
    """Custom MCP servers should use command/args/env from config."""
    from taskbrew.agents.provider import _build_mcp_dict
    servers = {
        "my-tool": MCPServerConfig(
            command="python",
            args=["-m", "my_tool"],
            env={"MY_VAR": "hello"},
            transport="stdio",
        ),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert "my-tool" in result
    assert result["my-tool"]["command"] == "python"
    assert result["my-tool"]["args"] == ["-m", "my_tool"]
    assert result["my-tool"]["env"]["MY_VAR"] == "hello"


def test_build_mcp_dict_env_interpolation():
    """${VAR} syntax in env values should be interpolated from os.environ."""
    from taskbrew.agents.provider import _build_mcp_dict
    os.environ["TEST_TOKEN_XYZ_AITEAM"] = "secret123"
    try:
        servers = {
            "github": MCPServerConfig(
                command="npx",
                args=["-y", "@anthropic/mcp-github"],
                env={"GITHUB_TOKEN": "${TEST_TOKEN_XYZ_AITEAM}", "STATIC": "value"},
            ),
        }
        result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
        assert result["github"]["env"]["GITHUB_TOKEN"] == "secret123"
        assert result["github"]["env"]["STATIC"] == "value"
    finally:
        del os.environ["TEST_TOKEN_XYZ_AITEAM"]


def test_build_mcp_dict_missing_env_var_kept():
    """${VAR} that doesn't exist in os.environ should be kept as-is."""
    from taskbrew.agents.provider import _build_mcp_dict
    servers = {
        "tool": MCPServerConfig(
            command="python", args=[],
            env={"TOKEN": "${NONEXISTENT_VAR_XYZABC}"},
        ),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert result["tool"]["env"]["TOKEN"] == "${NONEXISTENT_VAR_XYZABC}"


def test_build_mcp_dict_empty_command_skipped():
    """Non-builtin MCP server with empty command should be skipped."""
    from taskbrew.agents.provider import _build_mcp_dict

    servers = {
        "bad-tool": MCPServerConfig(command="", builtin=False),
        "good-tool": MCPServerConfig(command="python", args=["-m", "my_tool"]),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="test.db")
    assert "bad-tool" not in result
    assert "good-tool" in result
