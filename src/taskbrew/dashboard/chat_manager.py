"""Chat manager for bidirectional agent conversations."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk._errors import MessageParseError

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
        # audit 10 F#25: per-agent lock so two ``start_session`` calls
        # racing for the same agent_name cannot both spawn a subprocess.
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
        """Start a (or attach to an existing) chat session for an agent.

        Idempotent: if a healthy session for ``agent_name`` already
        exists, return it. The WS handler relies on this so that a
        second tab / page refresh / reconnect after disconnect can
        attach to a session another connection started, rather than
        the user seeing "already exists" errors.

        Stale sessions (``client is None`` or ``is_connected = False``)
        are torn down and recreated. This catches the case where a
        prior SDK process died but the dict entry survived.

        audit 10 F#25: take a per-agent lock around the
        check-then-spawn window so two concurrent callers for the same
        agent_name cannot both pass the ``if agent_name in self.sessions``
        check and then both spawn an SDK client (leaking the first one).
        """
        lock = await self._get_start_lock(agent_name)
        async with lock:
            existing = self.sessions.get(agent_name)
            if existing is not None:
                # Healthy: hand back the same object. The WS handler
                # uses identity to decide ownership.
                if existing.is_connected and existing.client is not None:
                    return existing
                # Stale entry from a crashed prior client. Best-effort
                # disconnect, then drop it and fall through to create
                # a fresh one.
                if existing.client is not None:
                    try:
                        await existing.client.disconnect()
                    except Exception:
                        pass
                existing.is_connected = False
                self.sessions.pop(agent_name, None)

            session_id = str(uuid.uuid4())[:8]
            opts = ClaudeAgentOptions(
                system_prompt=agent_config.system_prompt,
                allowed_tools=agent_config.allowed_tools,
                permission_mode=agent_config.permission_mode,
                env={"CLAUDECODE": ""},
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
        async with self._semaphore:
            session = self.sessions.get(agent_name)
            if not session or not session.client:
                raise ValueError(f"No active chat session for '{agent_name}'")
            if session.is_responding:
                raise ValueError(f"Agent '{agent_name}' is currently responding")

            # audit 10 F#26: append the user turn *before* attempting
            # the stream so the contextual-prompt builder can see it,
            # but on timeout / exception also record a paired error
            # assistant turn. Otherwise the history ends with an
            # orphan user message and every subsequent send replays
            # that unanswered turn in the context block, which the
            # model then tries to answer a second time.
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
                        timeout=300,  # 5 min
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

    @staticmethod
    def _build_contextual_prompt(session: ChatSession) -> str:
        """Build a prompt that includes conversation history for context.

        The ClaudeSDKClient uses a persistent subprocess that maintains its own
        conversation state across ``query()`` calls.  However, that internal
        context can be lost (e.g. auto-compaction on long conversations).  To
        be defensive we prepend a concise summary of the prior conversation
        turns so the model always has context, even if the subprocess state
        has been truncated.
        """
        history = session.history
        latest_message = history[-1].content

        # Only the latest message — no prior history to include.
        if len(history) <= 1:
            return latest_message

        # Build a brief conversation context from prior turns (skip the
        # latest message which we append verbatim at the end).
        prior_turns: list[str] = []
        for msg in history[:-1]:
            prefix = "User" if msg.role == "user" else "Assistant"
            # Truncate long messages to keep the context prompt reasonable.
            content = msg.content
            if len(content) > 300:
                content = content[:300] + "..."
            prior_turns.append(f"{prefix}: {content}")

        context_block = "\n".join(prior_turns)
        return (
            f"[Conversation context]\n{context_block}\n"
            f"[End context]\n\n{latest_message}"
        )

    async def _process_stream(
        self,
        session: ChatSession,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        on_tool_use: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> str:
        """Process the SDK stream, returning the full response text."""
        full_text = ""
        prompt = self._build_contextual_prompt(session)
        await session.client.query(prompt)

        async for message in self._receive_safe(session.client):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        full_text += block.text
                        if on_token:
                            await on_token(block.text)
            elif isinstance(message, ResultMessage):
                if hasattr(message, "result") and message.result:
                    full_text = message.result

        return full_text

    @staticmethod
    async def _receive_safe(client: ClaudeSDKClient):
        """Iterate receive_response(), skipping unknown message types like rate_limit_event.

        When MessageParseError is raised mid-stream (e.g. for rate_limit_event),
        the generator is dead. We restart iteration which picks up the next messages
        from the underlying transport until we get a ResultMessage.
        """
        while True:
            try:
                async for message in client.receive_response():
                    yield message
                    if isinstance(message, ResultMessage):
                        return
                # Generator exhausted normally
                return
            except MessageParseError:
                # Unknown message type encountered — restart iteration
                continue

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
