"""Chat manager for bidirectional agent conversations."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)

from ai_team.config import AgentConfig


@dataclass
class ChatMessage:
    """A single message in a chat conversation."""

    id: str
    role: str  # "user" or "assistant"
    content: str
    timestamp: str


@dataclass
class ChatSession:
    """An active chat session with an agent."""

    session_id: str
    agent_name: str
    agent_config: AgentConfig
    client: ClaudeSDKClient | None = None
    history: list[ChatMessage] = field(default_factory=list)
    is_connected: bool = False
    is_responding: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ChatManager:
    """Manages active chat sessions with agents."""

    def __init__(
        self,
        cli_path: str | None = None,
        project_dir: str | None = None,
        max_concurrent_chats: int = 6,
    ):
        self.cli_path = cli_path
        self.project_dir = project_dir
        self.sessions: dict[str, ChatSession] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent_chats)

    async def start_session(self, agent_name: str, agent_config: AgentConfig) -> ChatSession:
        """Start a new chat session for an agent."""
        if agent_name in self.sessions:
            raise ValueError(f"Chat session for '{agent_name}' already exists")

        session_id = str(uuid.uuid4())[:8]
        opts = ClaudeAgentOptions(
            system_prompt=agent_config.system_prompt,
            allowed_tools=agent_config.allowed_tools,
            permission_mode="bypassPermissions",
        )
        if self.cli_path:
            opts.cli_path = self.cli_path
        if self.project_dir:
            opts.cwd = self.project_dir

        client = ClaudeSDKClient(options=opts)
        await client.connect()

        session = ChatSession(
            session_id=session_id,
            agent_name=agent_name,
            agent_config=agent_config,
            client=client,
            is_connected=True,
        )
        self.sessions[agent_name] = session
        return session

    async def send_message(
        self,
        agent_name: str,
        user_message: str,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        on_tool_use: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> str:
        """Send a message and stream the response."""
        session = self.sessions.get(agent_name)
        if not session or not session.client:
            raise ValueError(f"No active chat session for '{agent_name}'")
        if session.is_responding:
            raise ValueError(f"Agent '{agent_name}' is currently responding")

        # Record user message
        user_msg = ChatMessage(
            id=str(uuid.uuid4())[:8],
            role="user",
            content=user_message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        session.history.append(user_msg)

        session.is_responding = True
        full_text = ""
        try:
            await session.client.query(user_message)

            async for message in session.client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            full_text += block.text
                            if on_token:
                                await on_token(block.text)
                elif isinstance(message, ResultMessage):
                    if hasattr(message, "result") and message.result:
                        full_text = message.result

            # Record assistant message
            assistant_msg = ChatMessage(
                id=str(uuid.uuid4())[:8],
                role="assistant",
                content=full_text,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            session.history.append(assistant_msg)
            return full_text
        finally:
            session.is_responding = False

    async def stop_session(self, agent_name: str) -> None:
        """Stop and remove a chat session."""
        session = self.sessions.get(agent_name)
        if not session:
            return
        if session.client:
            try:
                await session.client.disconnect()
            except Exception:
                pass
        session.is_connected = False
        del self.sessions[agent_name]

    def get_session(self, agent_name: str) -> ChatSession | None:
        """Get a session by agent name."""
        return self.sessions.get(agent_name)

    def get_history(self, agent_name: str) -> list[ChatMessage] | None:
        """Get the conversation history for an agent."""
        session = self.sessions.get(agent_name)
        if not session:
            return None
        return list(session.history)

    async def stop_all(self) -> None:
        """Stop all active sessions."""
        for agent_name in list(self.sessions):
            await self.stop_session(agent_name)
