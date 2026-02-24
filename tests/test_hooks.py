# tests/test_hooks.py
"""Tests for AgentRunner EventBus integration and streaming events.

SDK hooks (PreToolUse/PostToolUse) are disabled because they cause "Stream closed"
errors in the bundled CLI. Agent activity is instead streamed via the message
iterator in run(). The hook callback methods are kept as fire-and-forget helpers
but are NOT wired into ClaudeAgentOptions.
"""

import asyncio

import pytest

from ai_team.agents.base import AgentRunner, AgentStatus
from ai_team.config import AgentConfig
from ai_team.orchestrator.event_bus import EventBus


def _make_config(name: str = "test-agent") -> AgentConfig:
    return AgentConfig(
        name=name,
        role="Test agent",
        system_prompt="You are a test agent.",
        allowed_tools=["Read", "Write"],
    )


# -- Construction tests --


def test_agent_runner_accepts_event_bus():
    """AgentRunner can be constructed with an event_bus parameter."""
    bus = EventBus()
    runner = AgentRunner(_make_config(), event_bus=bus)
    assert runner.event_bus is bus


def test_agent_runner_event_bus_defaults_to_none():
    """AgentRunner without event_bus has event_bus=None."""
    runner = AgentRunner(_make_config())
    assert runner.event_bus is None


# -- build_options: hooks disabled, setting_sources empty --


def test_build_options_has_no_hooks():
    """build_options should NOT include hooks (disabled to avoid Stream closed errors)."""
    bus = EventBus()
    runner = AgentRunner(_make_config(), event_bus=bus)
    opts = runner.build_options()
    assert opts.hooks is None


def test_build_options_no_hooks_when_event_bus_is_none():
    """build_options should NOT set hooks when event_bus is None."""
    runner = AgentRunner(_make_config())
    opts = runner.build_options()
    assert opts.hooks is None


def test_build_options_sets_empty_setting_sources():
    """build_options should set setting_sources=[] to prevent loading global plugins."""
    runner = AgentRunner(_make_config())
    opts = runner.build_options()
    assert opts.setting_sources == []


def test_build_options_sets_bypass_permissions():
    """build_options should use bypassPermissions mode."""
    runner = AgentRunner(_make_config())
    opts = runner.build_options()
    assert opts.permission_mode == "bypassPermissions"


def test_build_options_unsets_claudecode_env():
    """build_options should set CLAUDECODE='' to allow nested SDK sessions."""
    runner = AgentRunner(_make_config())
    opts = runner.build_options()
    assert opts.env.get("CLAUDECODE") == ""


# -- Hook callback behavior (fire-and-forget, simplified data) --


async def test_pre_tool_use_callback_emits_event():
    """_on_pre_tool_use should emit a tool.pre_use event via fire-and-forget."""
    bus = EventBus()
    runner = AgentRunner(_make_config(name="coder"), event_bus=bus)

    hook_input = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_use_id": "tu_123",
        "session_id": "sess_abc",
        "cwd": "/tmp",
    }
    result = await runner._on_pre_tool_use(hook_input, "sess_abc", {"signal": None})

    assert result == {"continue_": True}

    # Fire-and-forget: allow asyncio task to complete
    await asyncio.sleep(0.05)

    history = bus.get_history("tool.pre_use")
    assert len(history) == 1
    event = history[0]
    assert event["agent_name"] == "coder"
    assert event["tool_name"] == "Bash"
    # tool_input is now stringified and truncated
    assert "command" in event["tool_input"]


async def test_post_tool_use_callback_emits_event():
    """_on_post_tool_use should emit a tool.post_use event via fire-and-forget."""
    bus = EventBus()
    runner = AgentRunner(_make_config(name="reviewer"), event_bus=bus)

    hook_input = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": "/tmp/foo.py"},
        "tool_use_id": "tu_456",
        "tool_response": "file contents here",
        "session_id": "sess_def",
        "cwd": "/tmp",
    }
    result = await runner._on_post_tool_use(hook_input, "sess_def", {"signal": None})

    assert result == {"continue_": True}

    await asyncio.sleep(0.05)

    history = bus.get_history("tool.post_use")
    assert len(history) == 1
    event = history[0]
    assert event["agent_name"] == "reviewer"
    assert event["tool_name"] == "Read"


async def test_hook_callbacks_return_continue_true():
    """Both hook callbacks should return continue_=True to let tool calls proceed."""
    bus = EventBus()
    runner = AgentRunner(_make_config(), event_bus=bus)

    minimal_input = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {},
        "tool_use_id": "tu_0",
        "session_id": "s",
        "cwd": "/",
    }
    pre_result = await runner._on_pre_tool_use(minimal_input, "s", {"signal": None})
    assert pre_result.get("continue_") is True

    minimal_input["hook_event_name"] = "PostToolUse"
    minimal_input["tool_response"] = ""
    post_result = await runner._on_post_tool_use(minimal_input, "s", {"signal": None})
    assert post_result.get("continue_") is True


async def test_event_bus_subscribers_receive_hook_events():
    """Subscribers on the EventBus should receive events emitted by hook callbacks."""
    bus = EventBus()
    runner = AgentRunner(_make_config(name="planner"), event_bus=bus)

    pre_events = []
    post_events = []

    async def on_pre(event):
        pre_events.append(event)

    async def on_post(event):
        post_events.append(event)

    bus.subscribe("tool.pre_use", on_pre)
    bus.subscribe("tool.post_use", on_post)

    pre_input = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Glob",
        "tool_input": {"pattern": "*.py"},
        "tool_use_id": "tu_789",
        "session_id": "sess_ghi",
        "cwd": "/project",
    }
    await runner._on_pre_tool_use(pre_input, "sess_ghi", {"signal": None})
    await asyncio.sleep(0.05)

    assert len(pre_events) == 1
    assert pre_events[0]["tool_name"] == "Glob"
    assert pre_events[0]["agent_name"] == "planner"

    post_input = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Glob",
        "tool_input": {"pattern": "*.py"},
        "tool_use_id": "tu_789",
        "tool_response": ["a.py", "b.py"],
        "session_id": "sess_ghi",
        "cwd": "/project",
    }
    await runner._on_post_tool_use(post_input, "sess_ghi", {"signal": None})
    await asyncio.sleep(0.05)

    assert len(post_events) == 1
    assert post_events[0]["tool_name"] == "Glob"
