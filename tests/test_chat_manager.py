"""Tests for ChatManager bidirectional agent conversations."""

from unittest.mock import patch

import pytest

from taskbrew.agents.provider_base import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from taskbrew.config import AgentConfig
from taskbrew.dashboard.chat_manager import ChatManager


@pytest.fixture
def agent_config():
    return AgentConfig(
        name="coder",
        role="coder",
        system_prompt="You are a coder.",
        allowed_tools=["Read", "Write"],
        model="claude-sonnet-4-6",
    )


@pytest.fixture
def chat_manager():
    return ChatManager(cli_path="/usr/bin/claude", project_dir="/tmp/test")


async def _fake_query(prompt, options, provider):
    yield AssistantMessage(content=[TextBlock(text="Hello! ")], session_id="s1")
    yield ResultMessage(result="Hello! I can help with that.", session_id="s1")


async def test_start_session_creates_session(chat_manager, agent_config):
    session = await chat_manager.start_session("coder", agent_config)

    assert session.agent_name == "coder"
    assert session.is_connected is True
    assert "coder" in chat_manager.sessions


async def test_start_session_is_idempotent_on_healthy_session(chat_manager, agent_config):
    first = await chat_manager.start_session("coder", agent_config)
    second = await chat_manager.start_session("coder", agent_config)

    assert second is first


async def test_start_session_recreates_stale_session(chat_manager, agent_config):
    first = await chat_manager.start_session("coder", agent_config)
    first.is_connected = False

    second = await chat_manager.start_session("coder", agent_config)

    assert second is not first
    assert second.is_connected is True


async def test_stop_session_disconnects(chat_manager, agent_config):
    await chat_manager.start_session("coder", agent_config)
    await chat_manager.stop_session("coder")

    assert "coder" not in chat_manager.sessions


async def test_stop_nonexistent_is_noop(chat_manager):
    await chat_manager.stop_session("nonexistent")


@patch("taskbrew.dashboard.chat_manager.sdk_query", side_effect=_fake_query)
async def test_send_message_records_history(mock_query, chat_manager, agent_config):
    await chat_manager.start_session("coder", agent_config)
    await chat_manager.send_message("coder", "Help me with X")

    history = chat_manager.get_history("coder")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "Help me with X"
    assert history[1].role == "assistant"
    assert history[1].content == "Hello! I can help with that."
    assert mock_query.call_args.kwargs["provider"] == "claude"


@patch("taskbrew.dashboard.chat_manager.sdk_query", side_effect=_fake_query)
async def test_send_message_uses_codex_provider_for_openai_model(
    mock_query, chat_manager,
):
    config = AgentConfig(
        name="coder",
        role="coder",
        system_prompt="You are a coder.",
        model="gpt-5.5",
        cli_provider="claude",
    )

    await chat_manager.start_session("coder", config)
    await chat_manager.send_message("coder", "Help")

    assert mock_query.call_args.kwargs["provider"] == "codex"


@patch("taskbrew.dashboard.chat_manager.sdk_query")
async def test_send_message_streams_tool_use(mock_query, chat_manager, agent_config):
    async def fake_query(prompt, options, provider):
        yield AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Read", input={"file_path": "a.py"})],
            session_id="s1",
        )
        yield ResultMessage(result="Done", session_id="s1")

    mock_query.side_effect = fake_query
    seen = []

    async def on_tool_use(name, tool_input):
        seen.append((name, tool_input))

    await chat_manager.start_session("coder", agent_config)
    await chat_manager.send_message("coder", "Read file", on_tool_use=on_tool_use)

    assert seen == [("Read", {"file_path": "a.py"})]


async def test_send_while_responding_raises(chat_manager, agent_config):
    await chat_manager.start_session("coder", agent_config)
    chat_manager.sessions["coder"].is_responding = True

    with pytest.raises(ValueError, match="currently responding"):
        await chat_manager.send_message("coder", "Another message")


async def test_send_to_nonexistent_raises(chat_manager):
    with pytest.raises(ValueError, match="No active chat session"):
        await chat_manager.send_message("coder", "Hello")


async def test_stop_all_cleans_up(chat_manager):
    config1 = AgentConfig(name="coder", role="coder", system_prompt="coder")
    config2 = AgentConfig(name="pm", role="pm", system_prompt="pm")
    await chat_manager.start_session("coder", config1)
    await chat_manager.start_session("pm", config2)

    assert len(chat_manager.sessions) == 2
    await chat_manager.stop_all()
    assert len(chat_manager.sessions) == 0
