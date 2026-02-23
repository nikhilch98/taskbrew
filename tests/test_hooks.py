# tests/test_hooks.py
"""Tests for PreToolUse/PostToolUse hooks wired to EventBus."""

import asyncio

import pytest
from claude_agent_sdk import HookMatcher

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


# -- build_options hook wiring --


def test_build_options_includes_hooks_when_event_bus_set():
    """build_options should include PreToolUse and PostToolUse hooks when event_bus is set."""
    bus = EventBus()
    runner = AgentRunner(_make_config(), event_bus=bus)
    opts = runner.build_options()
    assert opts.hooks is not None
    assert "PreToolUse" in opts.hooks
    assert "PostToolUse" in opts.hooks


def test_build_options_no_hooks_when_event_bus_is_none():
    """build_options should NOT set hooks when event_bus is None."""
    runner = AgentRunner(_make_config())
    opts = runner.build_options()
    assert opts.hooks is None


def test_hooks_use_hook_matcher_with_none_matcher():
    """Each hook entry should be a HookMatcher with matcher=None (match all tools)."""
    bus = EventBus()
    runner = AgentRunner(_make_config(), event_bus=bus)
    opts = runner.build_options()

    for event_name in ("PreToolUse", "PostToolUse"):
        matchers = opts.hooks[event_name]
        assert len(matchers) == 1
        m = matchers[0]
        assert isinstance(m, HookMatcher)
        assert m.matcher is None  # match all tools


def test_hooks_reference_correct_callbacks():
    """PreToolUse hook should reference _on_pre_tool_use, PostToolUse _on_post_tool_use."""
    bus = EventBus()
    runner = AgentRunner(_make_config(), event_bus=bus)
    opts = runner.build_options()

    pre_hooks = opts.hooks["PreToolUse"][0].hooks
    assert len(pre_hooks) == 1
    assert pre_hooks[0] == runner._on_pre_tool_use

    post_hooks = opts.hooks["PostToolUse"][0].hooks
    assert len(post_hooks) == 1
    assert post_hooks[0] == runner._on_post_tool_use


# -- Hook callback behavior --


async def test_pre_tool_use_callback_emits_event():
    """_on_pre_tool_use should emit a tool.pre_use event to the EventBus."""
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

    history = bus.get_history("tool.pre_use")
    assert len(history) == 1
    event = history[0]
    assert event["agent_name"] == "coder"
    assert event["tool_name"] == "Bash"
    assert event["tool_input"] == {"command": "ls"}
    assert event["tool_use_id"] == "tu_123"


async def test_post_tool_use_callback_emits_event():
    """_on_post_tool_use should emit a tool.post_use event to the EventBus."""
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

    history = bus.get_history("tool.post_use")
    assert len(history) == 1
    event = history[0]
    assert event["agent_name"] == "reviewer"
    assert event["tool_name"] == "Read"
    assert event["tool_input"] == {"file_path": "/tmp/foo.py"}
    assert event["tool_use_id"] == "tu_456"
    assert event["tool_response"] == "file contents here"


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
    # Allow asyncio tasks from EventBus.emit to complete
    await asyncio.sleep(0.01)

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
    await asyncio.sleep(0.01)

    assert len(post_events) == 1
    assert post_events[0]["tool_name"] == "Glob"
    assert post_events[0]["tool_response"] == ["a.py", "b.py"]
