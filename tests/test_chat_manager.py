"""Tests for ChatManager bidirectional agent conversations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from taskbrew.dashboard.chat_manager import ChatManager
from taskbrew.config import AgentConfig


@pytest.fixture
def agent_config():
    return AgentConfig(
        name="coder", role="coder", system_prompt="You are a coder.", allowed_tools=["Read", "Write"]
    )


@pytest.fixture
def chat_manager():
    return ChatManager(cli_path="/usr/bin/claude", project_dir="/tmp/test")


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_start_session_creates_client(mock_client_cls, chat_manager, agent_config):
    mock_client = AsyncMock()
    mock_client_cls.return_value = mock_client
    session = await chat_manager.start_session("coder", agent_config)
    assert session.agent_name == "coder"
    assert session.is_connected is True
    assert "coder" in chat_manager.sessions
    mock_client.connect.assert_awaited_once()


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_start_session_is_idempotent_on_healthy_session(
    mock_client_cls, chat_manager, agent_config,
):
    """start_session must be idempotent: calling it twice for the same
    agent returns the SAME session object so a page refresh / second
    tab / WS reconnect can attach to a session another connection
    started, instead of seeing a ValueError.
    """
    mock_client_cls.return_value = AsyncMock()
    first = await chat_manager.start_session("coder", agent_config)
    second = await chat_manager.start_session("coder", agent_config)
    assert second is first  # object identity — no new SDK client spawned
    # And only one SDK client was constructed across the two calls.
    assert mock_client_cls.call_count == 1


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_start_session_recreates_stale_session(
    mock_client_cls, chat_manager, agent_config,
):
    """If the existing session is stale (client crashed, is_connected
    flipped to False), start_session should tear it down and create a
    fresh one rather than handing back the dead reference."""
    mock_client_cls.return_value = AsyncMock()
    first = await chat_manager.start_session("coder", agent_config)
    # Simulate a stale entry: SDK process died but the dict survived.
    first.is_connected = False

    second = await chat_manager.start_session("coder", agent_config)
    assert second is not first
    assert second.is_connected is True
    # Two SDK clients constructed across the two calls.
    assert mock_client_cls.call_count == 2


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_stop_session_disconnects(mock_client_cls, chat_manager, agent_config):
    mock_client = AsyncMock()
    mock_client_cls.return_value = mock_client
    await chat_manager.start_session("coder", agent_config)
    await chat_manager.stop_session("coder")
    mock_client.disconnect.assert_awaited_once()
    assert "coder" not in chat_manager.sessions


async def test_stop_nonexistent_is_noop(chat_manager):
    await chat_manager.stop_session("nonexistent")  # Should not raise


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_send_message_records_history(mock_client_cls, chat_manager, agent_config):
    mock_client = AsyncMock()
    mock_client_cls.return_value = mock_client

    # Mock receive_response to yield a ResultMessage
    from claude_agent_sdk import ResultMessage

    mock_result = MagicMock(spec=ResultMessage)
    mock_result.result = "Hello! I can help with that."

    async def mock_receive_response():
        yield mock_result

    mock_client.receive_response = mock_receive_response

    await chat_manager.start_session("coder", agent_config)
    await chat_manager.send_message("coder", "Help me with X")

    history = chat_manager.get_history("coder")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "Help me with X"
    assert history[1].role == "assistant"


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_send_while_responding_raises(mock_client_cls, chat_manager, agent_config):
    mock_client_cls.return_value = AsyncMock()
    await chat_manager.start_session("coder", agent_config)
    chat_manager.sessions["coder"].is_responding = True
    with pytest.raises(ValueError, match="currently responding"):
        await chat_manager.send_message("coder", "Another message")


async def test_send_to_nonexistent_raises(chat_manager):
    with pytest.raises(ValueError, match="No active chat session"):
        await chat_manager.send_message("coder", "Hello")


@patch("taskbrew.dashboard.chat_manager.ClaudeSDKClient")
async def test_stop_all_cleans_up(mock_client_cls, chat_manager):
    mock_client_cls.return_value = AsyncMock()
    config1 = AgentConfig(name="coder", role="coder", system_prompt="coder")
    config2 = AgentConfig(name="pm", role="pm", system_prompt="pm")
    await chat_manager.start_session("coder", config1)
    await chat_manager.start_session("pm", config2)
    assert len(chat_manager.sessions) == 2
    await chat_manager.stop_all()
    assert len(chat_manager.sessions) == 0
