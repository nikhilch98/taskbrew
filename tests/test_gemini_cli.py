"""Tests for the direct Gemini CLI integration (taskbrew.agents.gemini_cli)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from taskbrew.agents.gemini_cli import (
    AssistantMessage,
    GeminiCLIError,
    GeminiCLINotFoundError,
    GeminiOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    _build_command,
    _build_prompt,
    _find_cli,
    query,
)


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_no_system_prompt(self):
        assert _build_prompt(None, "hello") == "hello"

    def test_with_system_prompt(self):
        result = _build_prompt("You are helpful.", "hello")
        assert "<system>" in result
        assert "You are helpful." in result
        assert "hello" in result
        assert result.endswith("hello")

    def test_empty_system_prompt(self):
        assert _build_prompt("", "hello") == "hello"


class TestBuildCommand:
    def test_basic_command(self):
        opts = GeminiOptions()
        cmd = _build_command("/usr/bin/gemini", "hello", opts)
        assert cmd[0] == "/usr/bin/gemini"
        assert "-p" in cmd
        assert cmd[cmd.index("-p") + 1] == "hello"
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "-y" in cmd

    def test_with_model(self):
        opts = GeminiOptions(model="gemini-3.1-pro-preview")
        cmd = _build_command("/usr/bin/gemini", "hello", opts)
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "gemini-3.1-pro-preview"

    def test_without_model(self):
        opts = GeminiOptions()
        cmd = _build_command("/usr/bin/gemini", "hello", opts)
        assert "-m" not in cmd


class TestFindCli:
    def test_explicit_path(self):
        assert _find_cli("/custom/gemini") == "/custom/gemini"

    @patch("shutil.which", return_value=None)
    def test_not_found(self, mock_which):
        with pytest.raises(GeminiCLINotFoundError, match="not found"):
            _find_cli(None)

    @patch("shutil.which", return_value="/opt/homebrew/bin/gemini")
    def test_found_via_which(self, mock_which):
        assert _find_cli(None) == "/opt/homebrew/bin/gemini"


# ---------------------------------------------------------------------------
# Unit tests: message types
# ---------------------------------------------------------------------------


class TestMessageTypes:
    """Verify message types satisfy the isinstance contract from base.py."""

    def test_text_block(self):
        block = TextBlock(text="hello")
        assert block.text == "hello"
        assert isinstance(block, TextBlock)

    def test_tool_use_block(self):
        block = ToolUseBlock(id="t1", name="read_file", input={"path": "/a"})
        assert block.name == "read_file"
        assert block.input == {"path": "/a"}
        assert isinstance(block, ToolUseBlock)

    def test_assistant_message(self):
        msg = AssistantMessage(
            content=[TextBlock(text="hi")],
            session_id="abc",
        )
        assert isinstance(msg, AssistantMessage)
        assert hasattr(msg, "session_id")
        assert hasattr(msg, "content")
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextBlock)

    def test_result_message(self):
        msg = ResultMessage(result="done", session_id="abc")
        assert isinstance(msg, ResultMessage)
        assert msg.result == "done"
        assert hasattr(msg, "total_cost_usd")
        assert hasattr(msg, "usage")
        assert hasattr(msg, "duration_api_ms")
        assert hasattr(msg, "num_turns")

    def test_result_message_defaults(self):
        msg = ResultMessage()
        assert msg.result == ""
        assert msg.total_cost_usd is None
        assert msg.usage is None
        assert msg.num_turns == 1
        assert msg.is_error is False


# ---------------------------------------------------------------------------
# Unit tests: provider integration
# ---------------------------------------------------------------------------


class TestProviderIntegration:
    def test_build_sdk_options_gemini(self):
        from taskbrew.agents.provider import build_sdk_options
        opts = build_sdk_options(
            provider="gemini",
            system_prompt="You are a PM.",
            model="gemini-3.1-pro-preview",
            cwd="/tmp",
        )
        assert isinstance(opts, GeminiOptions)
        assert opts.system_prompt == "You are a PM."
        assert opts.model == "gemini-3.1-pro-preview"
        assert opts.cwd == "/tmp"

    def test_get_message_types_gemini(self):
        from taskbrew.agents.provider import get_message_types
        types = get_message_types("gemini")
        assert types["AssistantMessage"] is AssistantMessage
        assert types["ResultMessage"] is ResultMessage
        assert types["TextBlock"] is TextBlock
        assert types["ToolUseBlock"] is ToolUseBlock


# ---------------------------------------------------------------------------
# Integration tests: query with mocked subprocess
# ---------------------------------------------------------------------------


def _make_mock_process(stdout_lines: list[str], returncode: int = 0):
    """Create a mock asyncio subprocess that yields the given stdout lines."""
    process = AsyncMock()
    process.returncode = None  # Initially running

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
    """Test parsing a simple assistant message with delta and result."""
    lines = [
        json.dumps({"type": "init", "session_id": "s1", "model": "gemini-3"}),
        json.dumps({"type": "message", "role": "user", "content": "hi"}),
        json.dumps({"type": "message", "role": "assistant", "content": "Hello", "delta": True}),
        json.dumps({"type": "message", "role": "assistant", "content": " world", "delta": True}),
        json.dumps({"type": "result", "status": "success", "stats": {
            "input_tokens": 100, "output_tokens": 10, "duration_ms": 500,
        }}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.gemini_cli._find_cli", return_value="/usr/bin/gemini"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="hi", options=GeminiOptions()):
            messages.append(msg)

    # Should get: flushed AssistantMessage with accumulated deltas, then ResultMessage
    assert len(messages) == 2
    assert isinstance(messages[0], AssistantMessage)
    assert messages[0].session_id == "s1"
    assert messages[0].content[0].text == "Hello world"
    assert isinstance(messages[1], ResultMessage)
    assert messages[1].result == "Hello world"
    assert messages[1].usage["input_tokens"] == 100
    assert messages[1].usage["output_tokens"] == 10


@pytest.mark.asyncio
async def test_query_with_tool_use():
    """Test parsing tool_use and tool_result events."""
    lines = [
        json.dumps({"type": "init", "session_id": "s2", "model": "gemini-3"}),
        json.dumps({"type": "message", "role": "assistant", "content": "Let me check.", "delta": True}),
        json.dumps({"type": "tool_use", "tool_name": "read_file", "tool_id": "t1", "parameters": {"path": "/a.py"}}),
        json.dumps({"type": "tool_result", "tool_id": "t1", "status": "success", "output": "content"}),
        json.dumps({"type": "message", "role": "assistant", "content": "Done.", "delta": True}),
        json.dumps({"type": "result", "status": "success", "stats": {"tool_calls": 1}}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.gemini_cli._find_cli", return_value="/usr/bin/gemini"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="read file", options=GeminiOptions()):
            messages.append(msg)

    # Expect: flushed text, tool_use message, flushed "Done.", result
    assert len(messages) == 4

    # First: flushed "Let me check." before tool_use
    assert isinstance(messages[0], AssistantMessage)
    assert messages[0].content[0].text == "Let me check."

    # Second: tool_use
    assert isinstance(messages[1], AssistantMessage)
    assert isinstance(messages[1].content[0], ToolUseBlock)
    assert messages[1].content[0].name == "read_file"
    assert messages[1].content[0].input == {"path": "/a.py"}

    # Third: "Done." flushed before result
    assert isinstance(messages[2], AssistantMessage)
    assert messages[2].content[0].text == "Done."

    # Fourth: result
    assert isinstance(messages[3], ResultMessage)
    assert messages[3].num_turns == 2  # tool_calls + 1


@pytest.mark.asyncio
async def test_query_skips_non_json_lines():
    """Non-JSON lines in stdout should be silently skipped."""
    lines = [
        "Some ANSI garbage \x1b[32m",
        json.dumps({"type": "init", "session_id": "s3"}),
        "Progress: 50%",
        json.dumps({"type": "message", "role": "assistant", "content": "ok", "delta": True}),
        json.dumps({"type": "result", "status": "success", "stats": {}}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.gemini_cli._find_cli", return_value="/usr/bin/gemini"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="test", options=GeminiOptions()):
            messages.append(msg)

    assert len(messages) == 2
    assert isinstance(messages[0], AssistantMessage)
    assert isinstance(messages[1], ResultMessage)


@pytest.mark.asyncio
async def test_query_process_error():
    """Non-zero exit without result event should raise GeminiCLIError."""
    lines = [
        json.dumps({"type": "init", "session_id": "s4"}),
    ]
    process = _make_mock_process(lines, returncode=1)
    process.stderr.read = AsyncMock(return_value=b"Something went wrong")

    with patch("taskbrew.agents.gemini_cli._find_cli", return_value="/usr/bin/gemini"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        with pytest.raises(GeminiCLIError, match="exited with code 1"):
            async for _ in query(prompt="fail", options=GeminiOptions()):
                pass


@pytest.mark.asyncio
async def test_query_cli_not_found():
    """Should raise GeminiCLINotFoundError when CLI is missing."""
    with patch("taskbrew.agents.gemini_cli._find_cli", side_effect=GeminiCLINotFoundError("not found")):
        with pytest.raises(GeminiCLINotFoundError):
            async for _ in query(prompt="test"):
                pass


@pytest.mark.asyncio
async def test_query_error_result():
    """Error status in result event should set is_error=True."""
    lines = [
        json.dumps({"type": "init", "session_id": "s5"}),
        json.dumps({"type": "message", "role": "assistant", "content": "Failed", "delta": True}),
        json.dumps({"type": "result", "status": "error", "stats": {}}),
    ]
    process = _make_mock_process(lines)

    with patch("taskbrew.agents.gemini_cli._find_cli", return_value="/usr/bin/gemini"), \
         patch("asyncio.create_subprocess_exec", return_value=process):
        messages = []
        async for msg in query(prompt="test", options=GeminiOptions()):
            messages.append(msg)

    result = messages[-1]
    assert isinstance(result, ResultMessage)
    assert result.is_error is True
    assert result.subtype == "error"
