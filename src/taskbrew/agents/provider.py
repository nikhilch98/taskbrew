"""Provider abstraction layer for Claude Code and Gemini CLI SDKs.

Dispatches to the correct SDK based on model name or explicit provider string.
Both SDKs expose a compatible ``query()`` async generator and similar message
types, so the abstraction is thin.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from taskbrew.config_loader import MCPServerConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP server helpers
# ---------------------------------------------------------------------------


def _interpolate_env(env: dict[str, str]) -> dict[str, str]:
    """Replace ${VAR} placeholders with values from os.environ."""
    result = {}
    for key, value in env.items():
        if isinstance(value, str) and "${" in value:
            result[key] = re.sub(
                r'\$\{(\w+)\}',
                lambda m: os.environ.get(m.group(1), m.group(0)),
                value,
            )
        else:
            result[key] = value
    return result


_BUILTIN_MCP_SERVERS = {
    "task-tools": {
        "module": "taskbrew.tools.task_tools",
        "env_key": "AI_TEAM_API_URL",
        "env_source": "api_url",
    },
    "intelligence-tools": {
        "module": "taskbrew.tools.intelligence_tools",
        "env_key": "AI_TEAM_DB_PATH",
        "env_source": "db_path",
    },
}


def _build_mcp_dict(
    servers: dict[str, MCPServerConfig],
    api_url: str = "http://127.0.0.1:8420",
    db_path: str = "data/tasks.db",
) -> dict[str, dict]:
    """Convert MCPServerConfig objects into SDK-compatible dicts."""
    env_sources = {"api_url": api_url, "db_path": db_path}
    result = {}
    for name, cfg in servers.items():
        if cfg.builtin and name in _BUILTIN_MCP_SERVERS:
            builtin = _BUILTIN_MCP_SERVERS[name]
            result[name] = {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", builtin["module"]],
                "env": {builtin["env_key"]: env_sources[builtin["env_source"]]},
            }
        elif not cfg.builtin:
            if not cfg.command or not cfg.command.strip():
                logger.warning("MCP server '%s' has empty command — skipping", name)
                continue
            result[name] = {
                "type": cfg.transport,
                "command": cfg.command,
                "args": cfg.args,
                "env": _interpolate_env(cfg.env),
            }
    return result


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """Registry for CLI agent providers.

    Manages built-in (Claude, Gemini) and user-defined providers.
    User providers can be added via YAML config or Python plugins.
    """

    def __init__(self):
        self._providers: dict[str, dict] = {}

    def register(self, name: str, detect_patterns: list[str], **kwargs):
        """Register a provider with its detection patterns."""
        self._providers[name] = {"detect_patterns": detect_patterns, **kwargs}

    def register_builtins(self):
        """Register the built-in Claude and Gemini providers."""
        self.register("claude", detect_patterns=["claude-*"], builtin=True)
        self.register("gemini", detect_patterns=["gemini-*"], builtin=True)

    def detect(self, model: str) -> str:
        """Detect provider from model name. Returns 'claude' as default."""
        for name, info in self._providers.items():
            if any(fnmatch(model, pat) for pat in info["detect_patterns"]):
                return name
        return "claude"

    def load_yaml_providers(self, providers_dir: Path) -> list[str]:
        """Load provider definitions from YAML files in a directory."""
        loaded = []
        if not providers_dir.is_dir():
            return loaded
        for yaml_file in sorted(providers_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                logger.warning("Skipping malformed provider file %s: %s", yaml_file.name, exc)
                continue
            if not data or "name" not in data:
                continue
            name = data["name"]
            detect = data.get("detect_models", [])
            self.register(name, detect_patterns=detect, yaml_config=data)
            loaded.append(name)
        return loaded

    def get(self, name: str) -> dict | None:
        """Get provider info by name."""
        return self._providers.get(name)

    @property
    def providers(self) -> dict[str, dict]:
        """All registered providers."""
        return dict(self._providers)


# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------


def detect_provider(model: str | None = None, cli_provider: str = "claude") -> str:
    """Infer the CLI provider from a model name or explicit setting.

    >>> detect_provider(model="gemini-3.1-pro-preview")
    'gemini'
    >>> detect_provider(model="claude-opus-4-6")
    'claude'
    >>> detect_provider(cli_provider="gemini")
    'gemini'
    """
    if model and model.startswith("gemini"):
        return "gemini"
    if model and model.startswith("claude"):
        return "claude"
    return cli_provider


# ---------------------------------------------------------------------------
# SDK option builders
# ---------------------------------------------------------------------------


def build_sdk_options(
    *,
    provider: str,
    system_prompt: str,
    model: str | None = None,
    max_turns: int | None = None,
    cwd: str | None = None,
    allowed_tools: list[str] | None = None,
    permission_mode: str = "default",
    api_url: str = "http://127.0.0.1:8420",
    db_path: str = "data/tasks.db",
    cli_path: str | None = None,
    mcp_servers: dict[str, MCPServerConfig] | None = None,
) -> Any:
    """Build SDK options for the given provider.

    Returns a ``ClaudeAgentOptions`` or ``GeminiOptions`` instance.
    """
    if provider == "gemini":
        from taskbrew.agents.gemini_cli import GeminiOptions

        opts = GeminiOptions(
            system_prompt=system_prompt,
        )
        if model:
            opts.model = model
        if max_turns:
            opts.max_turns = max_turns
        if cwd:
            opts.cwd = cwd
        if cli_path:
            opts.cli_path = cli_path
        return opts

    # Default: Claude
    from claude_agent_sdk import ClaudeAgentOptions

    opts = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=allowed_tools or [],
        permission_mode=permission_mode,
        env={"CLAUDECODE": ""},
        setting_sources=[],
        mcp_servers=_build_mcp_dict(
            mcp_servers or {},
            api_url=api_url,
            db_path=db_path,
        ),
    )
    if model:
        opts.model = model
    if max_turns:
        opts.max_turns = max_turns
    if cwd:
        opts.cwd = cwd
    if cli_path:
        opts.cli_path = cli_path
    return opts


# ---------------------------------------------------------------------------
# Unified query dispatcher
# ---------------------------------------------------------------------------


async def sdk_query(prompt: str, options: Any, provider: str) -> AsyncIterator:
    """Dispatch to the correct SDK's ``query()`` async generator."""
    if provider == "gemini":
        from taskbrew.agents.gemini_cli import query
        async for message in query(prompt=prompt, options=options):
            yield message
    else:
        from claude_agent_sdk import query
        async for message in query(prompt=prompt, options=options):
            yield message


# ---------------------------------------------------------------------------
# Message type helpers
# ---------------------------------------------------------------------------


def get_message_types(provider: str) -> dict[str, type]:
    """Return a dict of message type classes for isinstance checks.

    Keys: ``AssistantMessage``, ``ResultMessage``, ``TextBlock``, ``ToolUseBlock``.
    """
    if provider == "gemini":
        from taskbrew.agents.gemini_cli import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )
        return {
            "AssistantMessage": AssistantMessage,
            "ResultMessage": ResultMessage,
            "TextBlock": TextBlock,
            "ToolUseBlock": ToolUseBlock,
        }

    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
    )
    return {
        "AssistantMessage": AssistantMessage,
        "ResultMessage": ResultMessage,
        "TextBlock": TextBlock,
        "ToolUseBlock": ToolUseBlock,
    }
