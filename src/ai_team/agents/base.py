"""Base agent runner wrapping ClaudeSDKClient."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TYPE_CHECKING

from claude_agent_sdk import ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock, HookMatcher

from ai_team.config import AgentConfig

if TYPE_CHECKING:
    from ai_team.orchestrator.event_bus import EventBus


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
        self.session_id: str | None = None
        self._log: list[AgentEvent] = []

    def build_options(self, cwd: str | None = None) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions from agent config."""
        opts = ClaudeAgentOptions(
            system_prompt=self.config.system_prompt,
            allowed_tools=self.config.allowed_tools,
            permission_mode="bypassPermissions",
            env={"CLAUDECODE": ""},
        )
        if self.config.max_turns:
            opts.max_turns = self.config.max_turns
        if self.cli_path:
            opts.cli_path = self.cli_path
        if cwd or self.config.cwd:
            opts.cwd = str(cwd or self.config.cwd)
        if self.event_bus is not None:
            opts.hooks = {
                "PreToolUse": [
                    HookMatcher(matcher=None, hooks=[self._on_pre_tool_use]),
                ],
                "PostToolUse": [
                    HookMatcher(matcher=None, hooks=[self._on_post_tool_use]),
                ],
            }
        return opts

    async def _on_pre_tool_use(
        self, hook_input: Any, session_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Hook callback for PreToolUse events. Emits tool.pre_use to EventBus."""
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
        tool_use_id = hook_input.get("tool_use_id", "")
        assert self.event_bus is not None
        await self.event_bus.emit("tool.pre_use", {
            "agent_name": self.name,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
        })
        return {"continue_": True}

    async def _on_post_tool_use(
        self, hook_input: Any, session_id: str | None, context: Any
    ) -> dict[str, Any]:
        """Hook callback for PostToolUse events. Emits tool.post_use to EventBus."""
        tool_name = hook_input.get("tool_name", "")
        tool_input = hook_input.get("tool_input", {})
        tool_use_id = hook_input.get("tool_use_id", "")
        tool_response = hook_input.get("tool_response", None)
        assert self.event_bus is not None
        await self.event_bus.emit("tool.post_use", {
            "agent_name": self.name,
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_use_id": tool_use_id,
            "tool_response": tool_response,
        })
        return {"continue_": True}

    async def run(self, prompt: str, cwd: str | None = None) -> str:
        """Run the agent with a prompt and return the final result text."""
        from claude_agent_sdk import query

        self.status = AgentStatus.WORKING
        options = self.build_options(cwd=cwd)
        result_text = ""

        try:
            async for message in query(prompt=prompt, options=options):
                if hasattr(message, "subtype") and message.subtype == "init":
                    self.session_id = message.session_id

                if isinstance(message, ResultMessage):
                    result_text = message.result if hasattr(message, "result") else ""
                    self._log.append(AgentEvent(
                        agent_name=self.name,
                        event_type="complete",
                        data={"result": result_text},
                    ))
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            self._log.append(AgentEvent(
                                agent_name=self.name,
                                event_type="message",
                                data={"text": block.text},
                            ))
        except Exception as e:
            self.status = AgentStatus.ERROR
            self._log.append(AgentEvent(
                agent_name=self.name,
                event_type="error",
                data={"error": str(e)},
            ))
            raise
        finally:
            if self.status != AgentStatus.ERROR:
                self.status = AgentStatus.IDLE

        return result_text

    def get_log(self) -> list[AgentEvent]:
        return list(self._log)
