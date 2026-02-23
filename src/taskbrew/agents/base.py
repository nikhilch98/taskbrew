"""Base agent runner wrapping Claude/Gemini SDK via provider abstraction."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TYPE_CHECKING

from taskbrew.agents.provider import (
    build_sdk_options,
    detect_provider,
    get_message_types,
    sdk_query,
)
from taskbrew.config import AgentConfig

if TYPE_CHECKING:
    from taskbrew.orchestrator.event_bus import EventBus


class AgentStatus(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentEvent:
    """An event produced by an agent during execution."""
    agent_name: str
    event_type: str  # "message", "tool_use", "tool_result", "error", "complete"
    data: dict[str, Any] = field(default_factory=dict)


class AgentRunner:
    """Wraps the Claude Agent SDK to run a single agent with monitoring."""

    MAX_LOG_SIZE = 1000

    def __init__(
        self,
        config: AgentConfig,
        cli_path: str | None = None,
        event_bus: EventBus | None = None,
    ):
        self.config = config
        self.name = config.name
        self.status = AgentStatus.IDLE
        self.cli_path = cli_path
        self.event_bus = event_bus
        self.provider = detect_provider(
            model=config.model, cli_provider=config.cli_provider,
        )
        self.session_id: str | None = None
        self._log: list[AgentEvent] = []
        self.last_usage: dict | None = None

    def build_options(self, cwd: str | None = None) -> Any:
        """Build SDK options from agent config (provider-aware)."""
        effective_cwd = str(cwd or self.config.cwd) if (cwd or self.config.cwd) else None
        return build_sdk_options(
            provider=self.provider,
            system_prompt=self.config.system_prompt,
            model=self.config.model,
            max_turns=self.config.max_turns,
            cwd=effective_cwd,
            allowed_tools=self.config.allowed_tools,
            permission_mode=self.config.permission_mode,
            api_url=self.config.api_url,
            db_path=self.config.db_path,
            cli_path=self.cli_path,
            mcp_servers=self.config.mcp_servers,
        )

    async def _on_pre_tool_use(
        self, hook_input: Any, session_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Hook callback for PreToolUse events. Emits tool.pre_use to EventBus."""
        if self.event_bus is not None:
            tool_name = hook_input.get("tool_name", "")
            tool_input = hook_input.get("tool_input", {})
            asyncio.create_task(self.event_bus.emit("tool.pre_use", {
                "agent_name": self.name,
                "tool_name": tool_name,
                "tool_input": str(tool_input)[:200],
                "model": self.config.model,
            }))
        return {"continue_": True}

    async def _on_post_tool_use(
        self, hook_input: Any, session_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Hook callback for PostToolUse events. Emits tool.post_use to EventBus."""
        if self.event_bus is not None:
            tool_name = hook_input.get("tool_name", "")
            asyncio.create_task(self.event_bus.emit("tool.post_use", {
                "agent_name": self.name,
                "tool_name": tool_name,
                "model": self.config.model,
            }))
        return {"continue_": True}

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation (~4 chars per token)."""
        return len(text) // 4

    def _trim_context(self, context: str, max_tokens: int = 150000) -> str:
        """Trim context to fit within model's context window.

        Keeps the first 20% and last 60% of the allowed character budget,
        inserting a trimmed-marker in the middle.  The most recent context
        (at the end) is usually the most important, so it receives a larger
        share.
        """
        estimated = self._estimate_tokens(context)
        if estimated <= max_tokens:
            return context
        # Keep first 20% and last 60% (most recent context is most important)
        chars_limit = max_tokens * 4
        head_size = int(chars_limit * 0.2)
        tail_size = int(chars_limit * 0.6)
        return (
            context[:head_size]
            + "\n\n... [context trimmed] ...\n\n"
            + context[-tail_size:]
        )

    async def run(self, prompt: str, cwd: str | None = None) -> str:
        """Run the agent with a prompt and return the final result text."""
        mtypes = get_message_types(self.provider)
        ResultMessage = mtypes["ResultMessage"]
        AssistantMessage = mtypes["AssistantMessage"]
        TextBlock = mtypes["TextBlock"]
        ToolUseBlock = mtypes["ToolUseBlock"]

        self.status = AgentStatus.WORKING
        options = self.build_options(cwd=cwd)
        result_text = ""

        # Apply context trimming before sending to the SDK
        prompt = self._trim_context(prompt)

        try:
            async for message in sdk_query(prompt=prompt, options=options, provider=self.provider):
                if hasattr(message, "session_id"):
                    self.session_id = message.session_id

                if isinstance(message, ResultMessage):
                    result_text = (message.result or "") if hasattr(message, "result") else ""
                    self._log.append(AgentEvent(
                        agent_name=self.name,
                        event_type="complete",
                        data={"result": result_text},
                    ))
                    if len(self._log) > self.MAX_LOG_SIZE:
                        self._log = self._log[-self.MAX_LOG_SIZE:]
                    if self.event_bus:
                        await self.event_bus.emit("agent.result", {
                            "agent_name": self.name,
                            "result": result_text[:500],
                            "model": self.config.model,
                        })
                    self.last_usage = {
                        "cost_usd": getattr(message, "total_cost_usd", 0),
                        "usage": getattr(message, "usage", {}),
                        "duration_api_ms": getattr(message, "duration_api_ms", 0),
                        "num_turns": getattr(message, "num_turns", 0),
                    }
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            self._log.append(AgentEvent(
                                agent_name=self.name,
                                event_type="message",
                                data={"text": block.text},
                            ))
                            if len(self._log) > self.MAX_LOG_SIZE:
                                self._log = self._log[-self.MAX_LOG_SIZE:]
                            if self.event_bus:
                                await self.event_bus.emit("agent.text", {
                                    "agent_name": self.name,
                                    "text": block.text[:1000],
                                    "model": self.config.model,
                                })
                        elif isinstance(block, ToolUseBlock):
                            tool_name = block.name if hasattr(block, "name") else "unknown"
                            if self.event_bus:
                                await self.event_bus.emit("tool.pre_use", {
                                    "agent_name": self.name,
                                    "tool_name": tool_name,
                                    "tool_input": str(block.input)[:200] if hasattr(block, "input") else "",
                                    "model": self.config.model,
                                })
        except Exception as e:
            self.status = AgentStatus.ERROR
            self._log.append(AgentEvent(
                agent_name=self.name,
                event_type="error",
                data={"error": str(e)},
            ))
            if len(self._log) > self.MAX_LOG_SIZE:
                self._log = self._log[-self.MAX_LOG_SIZE:]
            raise
        finally:
            if self.status != AgentStatus.ERROR:
                self.status = AgentStatus.IDLE

        return result_text

    def get_log(self) -> list[AgentEvent]:
        return list(self._log)
