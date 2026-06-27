"""Tests for the stdio task-tools MCP server surface."""

import json

from taskbrew.tools.task_tools import _json_headers, build_task_tools_server


def test_json_headers_emit_project_scope_when_env_set(monkeypatch):
    """The other half of the concurrent-scoping seam: TASKBREW_PROJECT_ID in the
    subprocess env must surface as the X-Taskbrew-Project header so this agent's
    callbacks resolve its own project, not whichever one is focused."""
    monkeypatch.setenv("TASKBREW_PROJECT_ID", "beta-proj")
    headers = _json_headers()
    assert headers["X-Taskbrew-Project"] == "beta-proj"


def test_json_headers_omit_project_scope_when_env_unset(monkeypatch):
    """No env var → no scoping header (single-project callbacks stay unscoped)."""
    monkeypatch.delenv("TASKBREW_PROJECT_ID", raising=False)
    headers = _json_headers()
    assert "X-Taskbrew-Project" not in headers


def test_complete_task_tool_accepts_artifact_metadata():
    server = build_task_tools_server("http://example.test")

    complete_task = server._tool_manager._tools["complete_task"]
    params = complete_task.parameters["properties"]

    assert "summary" in params
    assert "artifact_paths" in params


def test_record_check_tool_is_exposed_to_cli_agents():
    server = build_task_tools_server("http://example.test")

    assert "record_check" in server._tool_manager._tools


def test_complete_task_tool_posts_artifact_payload(monkeypatch):
    server = build_task_tools_server("http://example.test")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({
                "status": "completed",
                "ingested_artifacts": ["notes.md"],
            }).encode()

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode())
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = server._tool_manager._tools["complete_task"].fn(
        "CD-001",
        summary="Implemented.",
        artifact_paths=["notes.md"],
    )

    assert captured["url"] == "http://example.test/api/tasks/CD-001/complete"
    assert captured["payload"] == {
        "status": "completed",
        "summary": "Implemented.",
        "artifact_paths": ["notes.md"],
    }
    assert "Ingested artifacts: notes.md" in result


def test_record_check_tool_posts_check_payload(monkeypatch):
    server = build_task_tools_server("http://example.test")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return json.dumps({
                "status": "ok",
                "ingested_artifacts": ["tests.log"],
            }).encode()

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode())
        captured["auth"] = req.headers.get("Authorization")
        return Response()

    monkeypatch.setattr("taskbrew.tools.task_tools._dashboard_auth_token", lambda: "")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = server._tool_manager._tools["record_check"].fn(
        "CD-001",
        "tests",
        "fail",
        details="1 failed",
        command="pytest",
        duration_ms=1200,
        artifact_paths=["tests.log"],
    )

    assert captured["url"] == "http://example.test/mcp/tools/record_check"
    assert captured["auth"] == "Bearer task-tools"
    assert captured["payload"] == {
        "task_id": "CD-001",
        "check_name": "tests",
        "status": "fail",
        "details": "1 failed",
        "command": "pytest",
        "duration_ms": 1200,
        "artifact_paths": ["tests.log"],
    }
    assert "Ingested artifacts: tests.log" in result
