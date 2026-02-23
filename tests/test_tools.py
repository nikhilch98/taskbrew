from taskbrew.tools.git_tools import build_git_tools_server


def test_git_tools_server_created():
    server = build_git_tools_server()
    assert server is not None


def test_task_tools_server_created():
    from taskbrew.tools.task_tools import build_task_tools_server
    from mcp.server.fastmcp import FastMCP
    server = build_task_tools_server(api_url="http://localhost:8420")
    assert isinstance(server, FastMCP)


def test_task_tools_server_has_create_task_tool():
    from taskbrew.tools.task_tools import build_task_tools_server
    server = build_task_tools_server(api_url="http://localhost:8420")
    # FastMCP stores tools in ._tool_manager._tools dict keyed by name
    tool_names = list(server._tool_manager._tools.keys())
    assert "create_task" in tool_names
