"""Tests for direct Codex CLI integration (taskbrew.agents.codex_cli)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from taskbrew.agents.codex_cli import (
    AssistantMessage,
    CodexCLIError,
    CodexCLINotFoundError,
    CodexOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    _build_command,
    _build_prompt,
    _find_cli,
    query,
)


class TestBuildPrompt:
    def test_no_system_prompt(self):
        assert _build_prompt(None, "hello") == "hello"

    def test_with_system_prompt(self):
        result = _build_prompt("You are helpful.", "hello")
        assert "<system>" in result
        assert "You are helpful." in result
        assert result.endswith("hello")


class TestBuildCommand:
    def test_basic_command(self):
        opts = CodexOptions(model="gpt-5.5", cwd="/tmp/project")
        cmd = _build_command("/usr/bin/codex", "hello", opts)
        assert cmd[:2] == ["/usr/bin/codex", "exec"]
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "--full-auto" in cmd
        assert "--ask-for-approval" not in cmd
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "gpt-5.5"
        assert "-C" in cmd
        assert cmd[cmd.index("-C") + 1] == "/tmp/project"
        assert cmd[-1] == "hello"

    def test_reasoning_effort_becomes_config_override(self):
        opts = CodexOptions(model="gpt-5.4", reasoning_effort="xhigh")
        cmd = _build_command("/usr/bin/codex", "hello", opts)
        joined = "\n".join(cmd)
        assert 'model_reasoning_effort="xhigh"' in joined

    def test_bypass_permissions_uses_codex_bypass_flag(self):
        opts = CodexOptions(permission_mode="bypassPermissions")
        cmd = _build_command("/usr/bin/codex", "hello", opts)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--full-auto" not in cmd
        assert "--ask-for-approval" not in cmd

    @patch("taskbrew.agents.codex_cli._git_metadata_writable_roots")
    def test_worktree_git_metadata_is_added_as_writable_root(self, mock_roots):
        mock_roots.return_value = ["/repo/.git"]

        opts = CodexOptions(cwd="/repo/.worktrees/coder-1")
        cmd = _build_command("/usr/bin/codex", "hello", opts)

        assert "--add-dir" in cmd
        assert cmd[cmd.index("--add-dir") + 1] == "/repo/.git"

    @patch("taskbrew.agents.codex_cli._git_metadata_writable_roots")
    def test_bypass_permissions_does_not_add_git_metadata_root(self, mock_roots):
        mock_roots.return_value = ["/repo/.git"]

        opts = CodexOptions(
            cwd="/repo/.worktrees/coder-1",
            permission_mode="bypassPermissions",
        )
        cmd = _build_command("/usr/bin/codex", "hello", opts)

        assert "--add-dir" not in cmd

    def test_mcp_servers_become_config_overrides(self):
        opts = CodexOptions(mcp_servers={
            "task-tools": {
                "type": "stdio",
                "command": "/usr/bin/python",
                "args": ["-m", "taskbrew.tools.task_tools"],
                "env": {"TASKBREW_API_URL": "http://127.0.0.1:8420"},
            }
        })
        cmd = _build_command("/usr/bin/codex", "hello", opts)
        joined = "\n".join(cmd)
        assert 'mcp_servers.task-tools.default_tools_approval_mode="approve"' in joined
        assert 'mcp_servers.task-tools.command="/usr/bin/python"' in joined
        assert 'mcp_servers.task-tools.args=["-m", "taskbrew.tools.task_tools"]' in joined
        assert '"TASKBREW_API_URL" = "http://127.0.0.1:8420"' in joined

    def test_http_mcp_servers_get_default_approval_mode(self):
        opts = CodexOptions(mcp_servers={
            "remote-tools": {
                "type": "http",
                "url": "http://127.0.0.1:9000/mcp",
            }
        })
        cmd = _build_command("/usr/bin/codex", "hello", opts)
        joined = "\n".join(cmd)
        assert 'mcp_servers.remote-tools.default_tools_approval_mode="approve"' in joined
        assert 'mcp_servers.remote-tools.url="http://127.0.0.1:9000/mcp"' in joined


class TestFindCli:
    def test_explicit_path(self):
        import os
        import sys

        assert _find_cli(sys.executable) == os.path.realpath(sys.executable)

    def test_explicit_path_rejected_when_missing(self):
        with pytest.raises(CodexCLINotFoundError, match="does not resolve"):
            _find_cli("/no/such/codex-12345")

    @patch("shutil.which", return_value=None)
    @patch("os.path.exists", return_value=False)
    def test_not_found(self, mock_exists, mock_which):
        with pytest.raises(CodexCLINotFoundError, match="not found"):
            _find_cli(None)


class TestProviderIntegration:
    def test_build_sdk_options_codex(self):
        from taskbrew.agents.provider import build_sdk_options

        opts = build_sdk_options(
            provider="codex",
            system_prompt="You are a coder.",
            model="gpt-5.5",
            reasoning_effort="high",
            cwd="/tmp",
        )
        assert isinstance(opts, CodexOptions)
        assert opts.system_prompt == "You are a coder."
        assert opts.model == "gpt-5.5"
        assert opts.reasoning_effort == "high"
        assert opts.cwd == "/tmp"

    def test_get_message_types_codex(self):
        from taskbrew.agents.provider import get_message_types

        types = get_message_types("codex")
        assert types["AssistantMessage"] is AssistantMessage
        assert types["ResultMessage"] is ResultMessage
        assert types["TextBlock"] is TextBlock
        assert types["ToolUseBlock"] is ToolUseBlock

    def test_detect_provider_codex_models(self):
        from taskbrew.agents.provider import detect_provider

        assert detect_provider(model="gpt-5.5") == "codex"
        assert detect_provider(model="gpt-5.3-codex") == "codex"
        assert detect_provider(model="gpt-5.3-codex-spark") == "codex"
        assert detect_provider(model="o3") == "codex"
        assert detect_provider(model="ollama-model") == "claude"
        assert detect_provider(cli_provider="codex") == "codex"


def _make_mock_process(stdout_lines: list[str], returncode: int = 0):
    process = AsyncMock()
    process.returncode = None

    async def _stdout_iter():
        for line in stdout_lines:
            yield (line + "\n").encode("utf-8")

    process.stdout = _stdout_iter()
    process.stderr = AsyncMock()
    process.stderr.read = AsyncMock(return_value=b"")

    async def _wait():
        process.returncode = returncode

    process.wait = _wait
    process.kill = MagicMock()
    return process


@pytest.mark.asyncio
async def test_query_simple_message():
    lines = [
        json.dumps({"type": "thread.started", "session_id": "s1"}),
        json.dumps({"type": "agent_message", "message": "Hello"}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.codex_cli._find_cli", return_value="/usr/bin/codex"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="hi", options=CodexOptions()):
            messages.append(msg)

    assert len(messages) == 2
    assert isinstance(messages[0], AssistantMessage)
    assert messages[0].content[0].text == "Hello"
    assert isinstance(messages[1], ResultMessage)
    assert messages[1].result == "Hello"
    assert messages[1].usage["input_tokens"] == 5


@pytest.mark.asyncio
async def test_query_result_uses_last_assistant_message():
    lines = [
        json.dumps({"type": "agent_message", "message": "I will inspect the repo."}),
        json.dumps({"type": "agent_message", "message": "Final answer."}),
        json.dumps({"type": "turn.completed"}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.codex_cli._find_cli", return_value="/usr/bin/codex"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="hi", options=CodexOptions()):
            messages.append(msg)

    assert isinstance(messages[-1], ResultMessage)
    assert messages[-1].result == "Final answer."


@pytest.mark.asyncio
async def test_query_item_completed_message_and_tool():
    lines = [
        json.dumps({"type": "item.started", "item": {
            "id": "t1",
            "type": "mcp_tool_call",
            "name": "mcp__task-tools__create_task",
            "arguments": {"title": "Do work"},
        }}),
        json.dumps({"type": "item.completed", "item": {
            "type": "agent_message",
            "content": [{"type": "text", "text": "Done"}],
        }}),
        json.dumps({"type": "turn.completed"}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.codex_cli._find_cli", return_value="/usr/bin/codex"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="work", options=CodexOptions()):
            messages.append(msg)

    assert isinstance(messages[0].content[0], ToolUseBlock)
    assert messages[0].content[0].name == "mcp__task-tools__create_task"
    assert messages[1].content[0].text == "Done"
    assert isinstance(messages[2], ResultMessage)


@pytest.mark.asyncio
async def test_query_does_not_duplicate_completed_tool_events():
    lines = [
        json.dumps({"type": "item.started", "item": {
            "id": "t1",
            "type": "mcp_tool_call",
            "name": "mcp__task-tools__create_task",
            "arguments": {"title": "Do work"},
        }}),
        json.dumps({"type": "item.completed", "item": {
            "id": "t1",
            "type": "mcp_tool_call",
            "name": "mcp__task-tools__create_task",
            "arguments": {"title": "Do work"},
            "result": {"content": [{"type": "text", "text": "Task created"}]},
        }}),
        json.dumps({"type": "turn.completed"}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.codex_cli._find_cli", return_value="/usr/bin/codex"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="work", options=CodexOptions()):
            messages.append(msg)

    tool_messages = [
        msg for msg in messages
        if isinstance(msg, AssistantMessage) and isinstance(msg.content[0], ToolUseBlock)
    ]
    assert len(tool_messages) == 1
    assert isinstance(messages[-1], ResultMessage)


@pytest.mark.asyncio
async def test_query_process_error():
    process = _make_mock_process([json.dumps({"type": "thread.started"})], returncode=1)
    process.stderr.read = AsyncMock(side_effect=[b"bad auth", b""])

    with patch("taskbrew.agents.codex_cli._find_cli", return_value="/usr/bin/codex"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        with pytest.raises(CodexCLIError, match="exited with code 1"):
            async for _ in query(prompt="fail", options=CodexOptions()):
                pass
