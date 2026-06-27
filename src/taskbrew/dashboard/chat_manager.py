"""Chat manager for bidirectional agent conversations."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from taskbrew.agents.provider import build_sdk_options, detect_provider, get_message_types, sdk_query
from taskbrew.config import AgentConfig


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
    client: Any | None = None
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
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._start_locks_mutex = asyncio.Lock()

    async def _get_start_lock(self, agent_name: str) -> asyncio.Lock:
        async with self._start_locks_mutex:
            lock = self._start_locks.get(agent_name)
            if lock is None:
                lock = asyncio.Lock()
                self._start_locks[agent_name] = lock
            return lock

    async def start_session(self, agent_name: str, agent_config: AgentConfig) -> ChatSession:
        """Start or attach to a chat session for an agent.

        Chat execution uses the same provider abstraction as background agents,
        so the selected role model determines Claude/Codex behavior.
        """
        lock = await self._get_start_lock(agent_name)
        async with lock:
            existing = self.sessions.get(agent_name)
            if existing is not None:
                if existing.is_connected:
                    return existing
                existing.is_connected = False
                self.sessions.pop(agent_name, None)

            session = ChatSession(
                session_id=str(uuid.uuid4())[:8],
                agent_name=agent_name,
                agent_config=agent_config,
                client=None,
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
        async with self._semaphore:
            session = self.sessions.get(agent_name)
            if not session:
                raise ValueError(f"No active chat session for '{agent_name}'")
            if session.is_responding:
                raise ValueError(f"Agent '{agent_name}' is currently responding")

            user_msg = ChatMessage(
                id=str(uuid.uuid4())[:8],
                role="user",
                content=user_message,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            session.history.append(user_msg)

            session.is_responding = True
            try:
                try:
                    full_text = await asyncio.wait_for(
                        self._process_stream(session, on_token, on_tool_use),
                        timeout=300,
                    )
                except asyncio.TimeoutError:
                    session.history.append(ChatMessage(
                        id=str(uuid.uuid4())[:8],
                        role="assistant",
                        content="[error: response timed out]",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))
                    return "error: Response timed out"
                except Exception:
                    session.history.append(ChatMessage(
                        id=str(uuid.uuid4())[:8],
                        role="assistant",
                        content="[error: stream failed]",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ))
                    raise

                session.history.append(ChatMessage(
                    id=str(uuid.uuid4())[:8],
                    role="assistant",
                    content=full_text,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ))
                return full_text
            finally:
                session.is_responding = False

    @staticmethod
    def _build_contextual_prompt(session: ChatSession) -> str:
        """Build a prompt that includes conversation history for context."""
        history = session.history
        latest_message = history[-1].content

        if len(history) <= 1:
            return latest_message

        prior_turns: list[str] = []
        for msg in history[:-1]:
            prefix = "User" if msg.role == "user" else "Assistant"
            content = msg.content
            if len(content) > 300:
                content = content[:300] + "..."
            prior_turns.append(f"{prefix}: {content}")

        context_block = "\n".join(prior_turns)
        return (
            f"[Conversation context]\n{context_block}\n"
            f"[End context]\n\n{latest_message}"
        )

    def _build_options(self, session: ChatSession) -> Any:
        config = session.agent_config
        provider = detect_provider(model=config.model, cli_provider=config.cli_provider)
        cwd = str(config.cwd or self.project_dir) if (config.cwd or self.project_dir) else None
        return build_sdk_options(
            provider=provider,
            system_prompt=config.system_prompt,
            model=config.model,
            max_turns=config.max_turns,
            cwd=cwd,
            allowed_tools=config.allowed_tools,
            permission_mode=config.permission_mode,
            api_url=config.api_url,
            db_path=config.db_path,
            cli_path=self.cli_path,
            mcp_servers=config.mcp_servers,
            agent_role=config.role,
            agent_instance=config.name,
        )

    async def _process_stream(
        self,
        session: ChatSession,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        on_tool_use: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> str:
        """Process the provider stream, returning the full response text."""
        config = session.agent_config
        provider = detect_provider(model=config.model, cli_provider=config.cli_provider)
        mtypes = get_message_types(provider)
        AssistantMessage = mtypes["AssistantMessage"]
        ResultMessage = mtypes["ResultMessage"]
        TextBlock = mtypes["TextBlock"]
        ToolUseBlock = mtypes["ToolUseBlock"]

        full_text = ""
        prompt = self._build_contextual_prompt(session)
        options = self._build_options(session)

        async for message in sdk_query(prompt=prompt, options=options, provider=provider):
            message_type = getattr(message, "type", "")
            if isinstance(message, AssistantMessage) or message_type == "assistant":
                for block in message.content:
                    block_type = getattr(block, "type", "")
                    if isinstance(block, TextBlock) or block_type == "text":
                        full_text += block.text
                        if on_token:
                            await on_token(block.text)
                    elif (isinstance(block, ToolUseBlock) or block_type == "tool_use") and on_tool_use:
                        await on_tool_use(block.name, block.input)
            elif isinstance(message, ResultMessage) or message_type == "result":
                if getattr(message, "result", None):
                    full_text = message.result

        return full_text

    async def stop_session(self, agent_name: str) -> None:
        """Stop and remove a chat session."""
        session = self.sessions.get(agent_name)
        if not session:
            return
        session.is_connected = False
        del self.sessions[agent_name]

    def get_session(self, agent_name: str) -> ChatSession | None:
        """Get a session by name."""
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
