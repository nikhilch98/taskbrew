"""Abstract base for CLI provider plugins.

Subclass ``ProviderPlugin`` to add support for a new CLI agent
(e.g., OpenAI Codex, Ollama).  See ``docs/extending.md`` for a guide.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class TextBlock:
    """A text content block in an assistant message."""
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    """A tool-use content block in an assistant message."""
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class AssistantMessage:
    """An assistant response message."""
    content: list[TextBlock | ToolUseBlock] = field(default_factory=list)
    session_id: str | None = None
    type: str = "assistant"


@dataclass
class ResultMessage:
    """Final result message from a provider query."""
    result: str = ""
    subtype: str = "success"
    is_error: bool = False
    session_id: str = ""
    num_turns: int = 1
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int = 0
    duration_api_ms: int = 0
    type: str = "result"


class ProviderPlugin(ABC):
    """Abstract base class for CLI agent providers.

    Subclass this to add support for a new CLI agent (e.g., Codex, Ollama).

    Required class attributes::

        name: str            # Short identifier ("codex")
        detect_patterns: list[str]  # fnmatch patterns for model names

    Required methods::

        build_options(**kwargs) -> Any
        async query(prompt, options) -> AsyncIterator[AssistantMessage | ResultMessage]
    """

    name: str = ""
    detect_patterns: list[str] = []

    @abstractmethod
    def build_options(self, **kwargs) -> Any:
        """Build provider-specific options from common parameters."""
        ...

    @abstractmethod
    async def query(
        self, prompt: str, options: Any,
    ) -> AsyncIterator[AssistantMessage | ResultMessage]:
        """Run a query and yield structured messages."""
        ...

    def get_message_types(self) -> dict[str, type]:
        """Return message type classes for isinstance checks."""
        return {
            "AssistantMessage": AssistantMessage,
            "ResultMessage": ResultMessage,
            "TextBlock": TextBlock,
            "ToolUseBlock": ToolUseBlock,
        }
