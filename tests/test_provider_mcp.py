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


def test_build_mcp_dict_threads_project_id():
    """agent_project must land in the builtin server env as TASKBREW_PROJECT_ID.

    This is the client half of the concurrent-scoping seam: the subprocess only
    knows which project it serves via this env var, which task_tools turns into
    the X-Taskbrew-Project callback header. If this threading breaks, agents send
    no header and every callback silently falls through to the focused project.
    """
    from taskbrew.agents.provider import _build_mcp_dict
    servers = {
        "task-tools": MCPServerConfig(builtin=True),
    }
    result = _build_mcp_dict(
        servers,
        api_url="http://localhost:8420",
        db_path="data/test.db",
        agent_project="alpha-proj",
    )
    assert result["task-tools"]["env"]["TASKBREW_PROJECT_ID"] == "alpha-proj"


def test_build_mcp_dict_omits_project_id_when_unset():
    """Without agent_project, no TASKBREW_PROJECT_ID is injected (single-project
    runs must not carry a stale scope)."""
    from taskbrew.agents.provider import _build_mcp_dict
    servers = {
        "task-tools": MCPServerConfig(builtin=True),
    }
    result = _build_mcp_dict(servers, api_url="http://localhost:8420", db_path="data/test.db")
    assert "TASKBREW_PROJECT_ID" not in result["task-tools"]["env"]


def test_build_mcp_dict_builtin_threads_tool_policy():
    """Built-in MCP tools should enforce the role's declared allowlist."""
    from taskbrew.agents.provider import _build_mcp_dict

    servers = {
        "task-tools": MCPServerConfig(builtin=True),
    }
    result = _build_mcp_dict(
        servers,
        api_url="http://localhost:8420",
        db_path="data/test.db",
        allowed_tools=["create_task", "list_tasks"],
        agent_role="pm",
        agent_instance="pm-1",
    )

    env = result["task-tools"]["env"]
    assert env["TASKBREW_ALLOWED_TOOLS"] == "create_task,list_tasks"
    assert env["TASKBREW_TOOL_ENFORCEMENT"] == "deny"
    assert env["TASKBREW_AGENT_ROLE"] == "pm"
    assert env["TASKBREW_AGENT_INSTANCE"] == "pm-1"


def test_tool_router_allows_client_style_mcp_tool_names():
    """MCP dispatch gates local names against client-style role allowlists."""
    from taskbrew.intelligence.tool_router import ToolRouter

    assert ToolRouter.is_tool_allowed(
        ["Read", "mcp__task-tools__create_task"],
        "create_task",
    )
    assert not ToolRouter.is_tool_allowed(
        ["Read", "mcp__task-tools__create_task"],
        "complete_task",
    )


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
