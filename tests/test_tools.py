import pytest
from ai_team.tools.task_tools import build_task_tools_server
from ai_team.tools.git_tools import build_git_tools_server


def test_task_tools_server_created():
    server = build_task_tools_server(db_path=":memory:")
    assert server is not None


def test_git_tools_server_created():
    server = build_git_tools_server()
    assert server is not None
