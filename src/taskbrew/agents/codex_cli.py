"""Direct Codex CLI integration via ``codex exec --json``.

The adapter normalizes Codex JSONL events into the same lightweight message
objects consumed by :class:`taskbrew.agents.base.AgentRunner`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


class CodexCLIError(Exception):
    """Base error for Codex CLI operations."""


class CodexCLINotFoundError(CodexCLIError):
    """Codex CLI binary not found."""


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    type: str = "tool_use"


@dataclass
class AssistantMessage:
    content: list[TextBlock | ToolUseBlock] = field(default_factory=list)
    session_id: str | None = None
    type: str = "assistant"


@dataclass
class ResultMessage:
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


@dataclass
class CodexOptions:
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int | None = None
    cwd: str | None = None
    cli_path: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    permission_mode: str = "default"
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    timeout_seconds: float | None = 1800.0


_WELL_KNOWN_PATHS = [
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
]


def _validate_cli_binary(candidate: str) -> str:
    if not candidate:
        raise CodexCLINotFoundError("cli_path is empty")
    resolved = os.path.realpath(candidate)
    if not os.path.isfile(resolved):
        raise CodexCLINotFoundError(
            f"cli_path {candidate!r} does not resolve to a regular file (realpath={resolved!r})"
        )
    if not os.access(resolved, os.X_OK):
        raise CodexCLINotFoundError(
            f"cli_path {resolved!r} is not executable by this process"
        )
    return resolved


def _find_cli(cli_path: str | None = None) -> str:
    """Locate the codex CLI binary."""
    if cli_path:
        return _validate_cli_binary(cli_path)
    found = shutil.which("codex")
    if found:
        return _validate_cli_binary(found)
    for path in _WELL_KNOWN_PATHS:
        if os.path.exists(path):
            return _validate_cli_binary(path)
    raise CodexCLINotFoundError(
        "Codex CLI not found. Install it with: npm install -g @openai/codex"
    )


def _build_prompt(system_prompt: str | None, user_prompt: str) -> str:
    if not system_prompt:
        return user_prompt
    return f"<system>\n{system_prompt}\n</system>\n\n{user_prompt}"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_array(values: list[Any]) -> str:
    return "[" + ", ".join(_toml_value(v) for v in values) + "]"


def _toml_inline_table(values: dict[str, Any]) -> str:
    return "{ " + ", ".join(
        f"{_toml_string(str(k))} = {_toml_value(v)}" for k, v in values.items()
    ) + " }"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return _toml_array(value)
    if isinstance(value, dict):
        return _toml_inline_table(value)
    return _toml_string(str(value))


def _codex_mcp_config_args(mcp_servers: dict[str, dict[str, Any]]) -> list[str]:
    """Return repeated ``-c`` overrides for Codex MCP server config."""
    args: list[str] = []
    for name, cfg in sorted((mcp_servers or {}).items()):
        if cfg.get("type", "stdio") != "stdio":
            url = cfg.get("url") or cfg.get("httpUrl")
            if url:
                args.extend(["-c", f'mcp_servers.{_toml_string(name)}.url={_toml_value(url)}'])
            continue
        command = cfg.get("command")
        if not command:
            logger.warning("Codex MCP server '%s' has no command -- skipping", name)
            continue
        prefix = f"mcp_servers.{_toml_string(name)}"
        args.extend(["-c", f"{prefix}.command={_toml_value(command)}"])
        if cfg.get("args"):
            args.extend(["-c", f"{prefix}.args={_toml_value(cfg['args'])}"])
        if cfg.get("env"):
            args.extend(["-c", f"{prefix}.env={_toml_value(cfg['env'])}"])
    return args


def _sandbox_for_permission_mode(permission_mode: str) -> str:
    if permission_mode in {"acceptEdits", "bypassPermissions"}:
        return "workspace-write"
    return "workspace-write"


def _build_command(
    cli_path: str,
    prompt: str,
    options: CodexOptions,
) -> list[str]:
    """Build a non-interactive Codex command."""
    cmd = [
        cli_path,
        "exec",
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--sandbox",
        _sandbox_for_permission_mode(options.permission_mode),
        "--ask-for-approval",
        "never",
    ]
    if options.model:
        cmd.extend(["-m", options.model])
    if options.cwd:
        cmd.extend(["-C", options.cwd])
    cmd.extend(_codex_mcp_config_args(options.mcp_servers))
    cmd.append(prompt)
    return cmd


async def _drain_stderr(process: asyncio.subprocess.Process, sink: list[bytes]) -> None:
    if process.stderr is None:
        return
    while True:
        chunk = await process.stderr.read(4096)
        if not chunk:
            break
        sink.append(chunk)


def _extract_text(event: dict[str, Any]) -> str:
    """Extract assistant text from known Codex JSON event shapes."""
    if isinstance(event.get("message"), str):
        return event["message"]
    item = event.get("item") or event.get("data") or {}
    if isinstance(item.get("text"), str):
        return item["text"]
    if isinstance(item.get("message"), str):
        return item["message"]
    if isinstance(item.get("content"), str):
        return item["content"]
    if isinstance(item.get("content"), list):
        parts = []
        for block in item["content"]:
            if isinstance(block, dict):
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_tool(event: dict[str, Any]) -> ToolUseBlock | None:
    item = event.get("item") or event.get("data") or {}
    item_type = str(item.get("type") or event.get("type") or "")
    if not any(token in item_type for token in ("tool", "command", "exec")):
        return None
    name = (
        item.get("name")
        or item.get("tool_name")
        or item.get("command")
        or item.get("type")
        or "unknown"
    )
    tool_input = item.get("arguments") or item.get("input") or item.get("args") or {}
    if isinstance(tool_input, str):
        tool_input = {"command": tool_input}
    return ToolUseBlock(
        id=str(item.get("id") or event.get("id") or ""),
        name=str(name),
        input=tool_input if isinstance(tool_input, dict) else {"value": tool_input},
    )


async def _query_impl(
    prompt: str,
    options: CodexOptions,
) -> AsyncIterator[AssistantMessage | ResultMessage]:
    opts = options
    cli_path = _find_cli(opts.cli_path)
    effective_prompt = _build_prompt(opts.system_prompt, prompt)
    cmd = _build_command(cli_path, effective_prompt, opts)

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=opts.cwd or None,
    )

    session_id: str | None = None
    accumulated_text = ""
    got_result = False
    start_time = time.monotonic()
    stderr_sink: list[bytes] = []
    stderr_task = asyncio.create_task(_drain_stderr(process, stderr_sink))

    try:
        assert process.stdout is not None
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping non-JSON Codex line: %s", line[:200])
                continue

            event_type = str(event.get("type") or "")
            session_id = (
                event.get("session_id")
                or event.get("conversation_id")
                or event.get("id")
                or session_id
            )

            if event_type in {"thread.started", "session.started"}:
                continue

            if event_type in {"agent_message", "assistant_message", "message"}:
                text = _extract_text(event)
                if text:
                    accumulated_text += text
                    yield AssistantMessage([TextBlock(text=text)], session_id=session_id)
                continue

            if event_type in {"item.started", "item.completed", "tool_call"}:
                item = event.get("item") or event.get("data") or {}
                item_type = str(item.get("type") or "")
                if "message" in item_type or item_type in {"agent_message", "assistant_message"}:
                    text = _extract_text(event)
                    if text:
                        accumulated_text += text
                        yield AssistantMessage([TextBlock(text=text)], session_id=session_id)
                    continue
                tool = _extract_tool(event)
                if tool:
                    yield AssistantMessage([tool], session_id=session_id)
                continue

            if event_type in {"turn.completed", "session.completed", "result"}:
                got_result = True
                elapsed = int((time.monotonic() - start_time) * 1000)
                usage = event.get("usage") or (event.get("data") or {}).get("usage") or {}
                yield ResultMessage(
                    result=accumulated_text,
                    session_id=session_id or "",
                    usage=usage,
                    duration_ms=elapsed,
                    duration_api_ms=elapsed,
                    num_turns=1,
                )
                continue

            if event_type in {"turn.failed", "session.failed", "error"}:
                got_result = True
                elapsed = int((time.monotonic() - start_time) * 1000)
                message = _extract_text(event) or str(event.get("error") or "Codex CLI failed")
                yield ResultMessage(
                    result=accumulated_text or message,
                    subtype="error",
                    is_error=True,
                    session_id=session_id or "",
                    duration_ms=elapsed,
                    duration_api_ms=elapsed,
                    num_turns=1,
                )
                continue

        await process.wait()
        try:
            await stderr_task
        except Exception:
            pass

        if not got_result:
            stderr_text = b"".join(stderr_sink).decode("utf-8", errors="replace").strip()
            if process.returncode and process.returncode != 0:
                raise CodexCLIError(
                    f"Codex CLI exited with code {process.returncode}: {stderr_text}"
                )
            if accumulated_text:
                yield ResultMessage(
                    result=accumulated_text,
                    session_id=session_id or "",
                    duration_api_ms=int((time.monotonic() - start_time) * 1000),
                )
    finally:
        if process.returncode is None:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
        if not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):
                pass


async def query(
    *, prompt: str, options: CodexOptions | None = None,
) -> AsyncIterator[AssistantMessage | ResultMessage]:
    """Run a Codex CLI query and yield structured messages."""
    opts = options or CodexOptions()
    generator = _query_impl(prompt, opts)
    if opts.timeout_seconds is None or opts.timeout_seconds <= 0:
        async for message in generator:
            yield message
        return

    deadline = time.monotonic() + opts.timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await generator.aclose()
            raise CodexCLIError(
                f"Codex CLI wall-clock timeout after {opts.timeout_seconds}s"
            )
        try:
            message = await asyncio.wait_for(generator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            await generator.aclose()
            raise CodexCLIError(
                f"Codex CLI wall-clock timeout after {opts.timeout_seconds}s"
            )
        yield message
