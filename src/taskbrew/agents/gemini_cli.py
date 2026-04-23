"""Direct Gemini CLI integration via --output-format stream-json.

Spawns the Gemini CLI as a subprocess with structured JSON streaming output,
eliminating the need for the gemini-cli-sdk package and its OpenAI dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GeminiCLIError(Exception):
    """Base error for Gemini CLI operations."""


class GeminiCLINotFoundError(GeminiCLIError):
    """Gemini CLI binary not found."""


# ---------------------------------------------------------------------------
# Message types (matching the interface contract consumed by base.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class GeminiOptions:
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int | None = None
    cwd: str | None = None
    cli_path: str | None = None
    # audit 02 F#1: wall-clock timeout in seconds for the entire CLI run.
    # Defaults to 30 minutes, matching the typical max_execution_time in
    # role configs. Set to None to disable (not recommended).
    timeout_seconds: float | None = 1800.0


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

_WELL_KNOWN_PATHS = [
    "/opt/homebrew/bin/gemini",
    "/usr/local/bin/gemini",
]


def _validate_cli_binary(candidate: str) -> str:
    """Validate that *candidate* is safe to spawn as a subprocess.

    audit 02 F#4: previously cli_path flowed straight into
    ``create_subprocess_exec`` with zero validation. A mis-configured
    role (or any config-injection sink) could point cli_path at an
    arbitrary executable, or at a symlink to a non-executable file that
    would hang on open. We now require:

    - candidate resolves via os.path.realpath (follows symlinks) to an
      EXISTING regular file
    - candidate is os.access()-executable by this process

    Returns the resolved absolute path so the caller gets a stable
    binary handle. Raises GeminiCLINotFoundError on failure.
    """
    import os
    if not candidate:
        raise GeminiCLINotFoundError("cli_path is empty")
    resolved = os.path.realpath(candidate)
    if not os.path.isfile(resolved):
        raise GeminiCLINotFoundError(
            f"cli_path {candidate!r} does not resolve to a regular file (realpath={resolved!r})"
        )
    if not os.access(resolved, os.X_OK):
        raise GeminiCLINotFoundError(
            f"cli_path {resolved!r} is not executable by this process"
        )
    return resolved


def _find_cli(cli_path: str | None = None) -> str:
    """Locate the gemini CLI binary."""
    if cli_path:
        return _validate_cli_binary(cli_path)
    found = shutil.which("gemini")
    if found:
        return _validate_cli_binary(found)
    for path in _WELL_KNOWN_PATHS:
        if shutil.which(path):
            return _validate_cli_binary(path)
    raise GeminiCLINotFoundError(
        "Gemini CLI not found. Install it with: npm install -g @google/gemini-cli"
    )


def _build_prompt(system_prompt: str | None, user_prompt: str) -> str:
    """Prepend system prompt using XML tags if provided."""
    if not system_prompt:
        return user_prompt
    return f"<system>\n{system_prompt}\n</system>\n\n{user_prompt}"


def _build_command(
    cli_path: str,
    prompt: str,
    options: GeminiOptions,
) -> list[str]:
    """Build the CLI command list."""
    cmd = [cli_path, "-p", prompt, "--output-format", "stream-json", "-y"]
    if options.model:
        cmd.extend(["-m", options.model])
    return cmd


# ---------------------------------------------------------------------------
# Stream-JSON parser and query generator
# ---------------------------------------------------------------------------


async def _drain_stderr(process: asyncio.subprocess.Process, sink: list[bytes]) -> None:
    """Concurrently drain stderr into *sink* so the child cannot deadlock
    by filling the stderr pipe buffer while we wait on stdout.

    audit 02 F#2: previously stderr was read only *after* process.wait()
    returned. When the child emitted more than ~64 KiB of stderr
    (verbose mode, tracebacks, warning spam) the pipe buffer filled, the
    child blocked on write, and the parent blocked forever waiting for
    a result on stdout -- the classic deadlock. Draining in parallel
    keeps the pipe empty and also lets us surface the tail of stderr
    in error messages.
    """
    if process.stderr is None:
        return
    while True:
        chunk = await process.stderr.read(4096)
        if not chunk:
            break
        sink.append(chunk)


async def _query_impl(
    prompt: str,
    options: GeminiOptions,
) -> AsyncIterator[AssistantMessage | ResultMessage]:
    """Actual query body; wrapped by :func:`query` for the timeout."""
    opts = options
    cli_path = _find_cli(opts.cli_path)
    effective_prompt = _build_prompt(opts.system_prompt, prompt)
    cmd = _build_command(cli_path, effective_prompt, opts)

    cwd = opts.cwd or None
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    session_id: str | None = None
    pending_text = ""
    accumulated_text = ""
    got_result = False
    start_time = time.monotonic()

    # Concurrent stderr drainer: audit 02 F#2.
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
                logger.debug("Skipping non-JSON line: %s", line[:200])
                continue

            event_type = event.get("type", "")

            if event_type == "init":
                session_id = event.get("session_id")

            elif event_type == "message" and event.get("role") == "assistant":
                content = event.get("content", "")
                is_delta = event.get("delta", False)

                if is_delta:
                    pending_text += content
                    accumulated_text += content
                else:
                    # Non-delta message: flush pending first, then yield this
                    if pending_text:
                        yield AssistantMessage(
                            content=[TextBlock(text=pending_text)],
                            session_id=session_id,
                        )
                        pending_text = ""
                    accumulated_text += content
                    yield AssistantMessage(
                        content=[TextBlock(text=content)],
                        session_id=session_id,
                    )

            elif event_type == "tool_use":
                # Flush pending text before tool use
                if pending_text:
                    yield AssistantMessage(
                        content=[TextBlock(text=pending_text)],
                        session_id=session_id,
                    )
                    pending_text = ""

                yield AssistantMessage(
                    content=[ToolUseBlock(
                        id=event.get("tool_id", ""),
                        name=event.get("tool_name", "unknown"),
                        input=event.get("parameters", {}),
                    )],
                    session_id=session_id,
                )

            elif event_type == "result":
                # Flush any remaining pending text
                if pending_text:
                    yield AssistantMessage(
                        content=[TextBlock(text=pending_text)],
                        session_id=session_id,
                    )
                    pending_text = ""

                got_result = True
                stats = event.get("stats", {})
                elapsed = int((time.monotonic() - start_time) * 1000)
                status = event.get("status", "success")

                yield ResultMessage(
                    result=accumulated_text,
                    subtype=status,
                    is_error=(status != "success"),
                    session_id=session_id or "",
                    num_turns=max(stats.get("tool_calls", 0) + 1, 1),
                    total_cost_usd=None,
                    usage={
                        "input_tokens": stats.get("input_tokens", 0),
                        "output_tokens": stats.get("output_tokens", 0),
                    },
                    duration_ms=stats.get("duration_ms", elapsed),
                    duration_api_ms=stats.get("duration_ms", elapsed),
                )

        # Wait for process to finish
        await process.wait()

        # Ensure stderr is fully drained before we read it.
        try:
            await stderr_task
        except Exception:
            pass

        if not got_result:
            # Process ended without a result event
            stderr_text = b"".join(stderr_sink).decode("utf-8", errors="replace").strip()
            if process.returncode and process.returncode != 0:
                raise GeminiCLIError(
                    f"Gemini CLI exited with code {process.returncode}: {stderr_text}"
                )
            # Yield a synthetic result if we got text but no result event
            if accumulated_text:
                yield ResultMessage(
                    result=accumulated_text,
                    session_id=session_id or "",
                    duration_api_ms=int((time.monotonic() - start_time) * 1000),
                )

    finally:
        # Kill the process if it is still alive (e.g. on cancellation or
        # generator close). Drain the stderr task so it does not leak.
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
    *, prompt: str, options: GeminiOptions | None = None,
) -> AsyncIterator[AssistantMessage | ResultMessage]:
    """Run a Gemini CLI query and yield structured messages.

    Spawns the CLI with ``--output-format stream-json`` and parses the
    line-delimited JSON events into message dataclasses compatible with
    the provider abstraction in ``base.py``.

    audit 02 F#1/F#2: enforces a wall-clock timeout (default 30 min via
    ``GeminiOptions.timeout_seconds``) and drains stderr concurrently
    with stdout to prevent pipe-buffer deadlock.
    """
    opts = options or GeminiOptions()
    generator = _query_impl(prompt, opts)
    if opts.timeout_seconds is None or opts.timeout_seconds <= 0:
        # Timeout explicitly disabled -- just stream through.
        async for message in generator:
            yield message
        return

    deadline = time.monotonic() + opts.timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await generator.aclose()
            raise GeminiCLIError(
                f"Gemini CLI wall-clock timeout after {opts.timeout_seconds}s"
            )
        try:
            message = await asyncio.wait_for(generator.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            await generator.aclose()
            raise GeminiCLIError(
                f"Gemini CLI wall-clock timeout after {opts.timeout_seconds}s"
            )
        yield message
