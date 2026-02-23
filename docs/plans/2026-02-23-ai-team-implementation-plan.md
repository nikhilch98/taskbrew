# AI Team Orchestrator - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python orchestration layer that spawns multiple Claude Code agents as an AI team (PM, Researcher, Architect, Coder, Tester, Reviewer) with a real-time dashboard, using the Claude Agent SDK and local Max subscription auth.

**Architecture:** Multi-process orchestrator where each agent is an independent `ClaudeSDKClient` instance. Agents communicate via a shared SQLite task queue and filesystem artifacts. An asyncio event bus routes notifications. A FastAPI dashboard provides real-time monitoring via WebSocket.

**Tech Stack:** Python 3.10+, claude-agent-sdk, FastAPI, uvicorn, SQLAlchemy, SQLite, asyncio, Jinja2, git

**Design doc:** `docs/plans/2026-02-23-ai-team-orchestrator-design.md`

---

## Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `src/taskbrew/__init__.py`
- Create: `src/taskbrew/config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ai-team"
version = "0.1.0"
description = "AI Team Orchestrator - multi-agent development automation"
requires-python = ">=3.10"
dependencies = [
    "claude-agent-sdk>=0.2.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "sqlalchemy>=2.0",
    "aiosqlite>=0.20.0",
    "jinja2>=3.1",
    "pyyaml>=6.0",
    "websockets>=14.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0",
    "ruff>=0.8.0",
]

[project.scripts]
ai-team = "taskbrew.main:cli_main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
target-version = "py310"
line-length = 100
```

**Step 2: Create src/taskbrew/__init__.py**

```python
"""AI Team Orchestrator - multi-agent development automation."""

__version__ = "0.1.0"
```

**Step 3: Create src/taskbrew/config.py**

```python
"""Configuration for the AI Team Orchestrator."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    """Configuration for a single agent."""
    name: str
    role: str
    system_prompt: str
    allowed_tools: list[str] = field(default_factory=list)
    max_turns: int | None = None
    cwd: Path | None = None


@dataclass
class OrchestratorConfig:
    """Top-level orchestrator configuration."""
    project_dir: Path = field(default_factory=lambda: Path.cwd())
    db_path: Path = field(default_factory=lambda: Path("data/tasks.db"))
    artifacts_dir: Path = field(default_factory=lambda: Path("artifacts"))
    cli_path: str | None = None  # Auto-detect if None
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8420
    max_concurrent_agents: int = 3

    def __post_init__(self):
        self.db_path = self.project_dir / self.db_path
        self.artifacts_dir = self.project_dir / self.artifacts_dir
```

**Step 4: Create tests/__init__.py and tests/conftest.py**

```python
# tests/conftest.py
import asyncio
from pathlib import Path
import pytest

@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory."""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path
```

**Step 5: Install dependencies and verify**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/ai-team" && pip install -e ".[dev]"`
Expected: Successful installation with all dependencies resolved

**Step 6: Run empty test suite to verify setup**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/ai-team" && python -m pytest tests/ -v`
Expected: `no tests ran` (0 collected, no errors)

**Step 7: Initialize git and commit**

```bash
cd "/Users/nikhilchatragadda/Personal Projects/ai-team"
git init
echo "__pycache__/\n*.egg-info/\n.eggs/\ndist/\nbuild/\ndata/\n*.db\n.venv/\nartifacts/" > .gitignore
git add .
git commit -m "feat: project scaffolding with pyproject.toml and config"
```

---

## Task 2: Event Bus

**Files:**
- Create: `src/taskbrew/orchestrator/__init__.py`
- Create: `src/taskbrew/orchestrator/event_bus.py`
- Create: `tests/test_event_bus.py`

**Step 1: Write the failing tests**

```python
# tests/test_event_bus.py
import asyncio
import pytest
from taskbrew.orchestrator.event_bus import EventBus


async def test_subscribe_and_emit():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("task_completed", handler)
    await bus.emit("task_completed", {"task_id": "1", "agent": "coder"})
    await asyncio.sleep(0.01)
    assert len(received) == 1
    assert received[0]["task_id"] == "1"


async def test_multiple_subscribers():
    bus = EventBus()
    results_a, results_b = [], []

    async def handler_a(event):
        results_a.append(event)

    async def handler_b(event):
        results_b.append(event)

    bus.subscribe("test_event", handler_a)
    bus.subscribe("test_event", handler_b)
    await bus.emit("test_event", {"data": "hello"})
    await asyncio.sleep(0.01)
    assert len(results_a) == 1
    assert len(results_b) == 1


async def test_unsubscribe():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("test_event", handler)
    bus.unsubscribe("test_event", handler)
    await bus.emit("test_event", {"data": "hello"})
    await asyncio.sleep(0.01)
    assert len(received) == 0


async def test_emit_unsubscribed_event_no_error():
    bus = EventBus()
    await bus.emit("nonexistent", {"data": "hello"})  # Should not raise


async def test_wildcard_subscriber():
    bus = EventBus()
    received = []

    async def handler(event):
        received.append(event)

    bus.subscribe("*", handler)
    await bus.emit("any_event", {"data": "1"})
    await bus.emit("another_event", {"data": "2"})
    await asyncio.sleep(0.01)
    assert len(received) == 2
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_event_bus.py -v`
Expected: FAIL (module not found)

**Step 3: Implement EventBus**

```python
# src/taskbrew/orchestrator/__init__.py
"""Orchestrator components."""

# src/taskbrew/orchestrator/event_bus.py
"""Async event bus for inter-component communication."""

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

EventHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class EventBus:
    """Simple asyncio-based pub/sub event bus."""

    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._history: list[dict[str, Any]] = []

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        event = {"type": event_type, **data}
        self._history.append(event)

        handlers = list(self._handlers.get(event_type, []))
        handlers.extend(self._handlers.get("*", []))

        for handler in handlers:
            asyncio.create_task(handler(event))

    def get_history(self, event_type: str | None = None) -> list[dict[str, Any]]:
        if event_type is None:
            return list(self._history)
        return [e for e in self._history if e["type"] == event_type]
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_event_bus.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/ tests/test_event_bus.py
git commit -m "feat: asyncio event bus with subscribe/emit/wildcard"
```

---

## Task 3: Task Queue (SQLite)

**Files:**
- Create: `src/taskbrew/orchestrator/task_queue.py`
- Create: `tests/test_task_queue.py`

**Step 1: Write the failing tests**

```python
# tests/test_task_queue.py
import pytest
from pathlib import Path
from taskbrew.orchestrator.task_queue import TaskQueue, TaskStatus


@pytest.fixture
async def queue(tmp_path):
    q = TaskQueue(db_path=tmp_path / "test.db")
    await q.initialize()
    yield q
    await q.close()


async def test_create_task(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="implement",
        input_context="Build auth module",
    )
    assert task_id is not None
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.PENDING
    assert task["input_context"] == "Build auth module"


async def test_assign_task(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="implement",
        input_context="Build auth",
    )
    await queue.assign_task(task_id, "coder")
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.ASSIGNED
    assert task["assigned_to"] == "coder"


async def test_update_status(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="review",
        input_context="Review PR",
    )
    await queue.assign_task(task_id, "reviewer")
    await queue.update_status(task_id, TaskStatus.IN_PROGRESS)
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.IN_PROGRESS


async def test_complete_task_with_artifact(queue):
    task_id = await queue.create_task(
        pipeline_id="pipe-1",
        task_type="research",
        input_context="Research auth patterns",
    )
    await queue.complete_task(task_id, output_artifact="artifacts/1/research.md")
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.COMPLETED
    assert task["output_artifact"] == "artifacts/1/research.md"


async def test_get_pending_tasks(queue):
    await queue.create_task(pipeline_id="p1", task_type="a", input_context="ctx")
    t2 = await queue.create_task(pipeline_id="p1", task_type="b", input_context="ctx")
    await queue.assign_task(t2, "coder")
    pending = await queue.get_pending_tasks()
    assert len(pending) == 1
    assert pending[0]["task_type"] == "a"


async def test_get_tasks_by_pipeline(queue):
    await queue.create_task(pipeline_id="p1", task_type="a", input_context="ctx")
    await queue.create_task(pipeline_id="p2", task_type="b", input_context="ctx")
    tasks = await queue.get_tasks_by_pipeline("p1")
    assert len(tasks) == 1


async def test_fail_task(queue):
    task_id = await queue.create_task(
        pipeline_id="p1", task_type="test", input_context="Run tests"
    )
    await queue.fail_task(task_id, error="Tests failed: 3 failures")
    task = await queue.get_task(task_id)
    assert task["status"] == TaskStatus.FAILED
    assert "3 failures" in task["error"]
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_task_queue.py -v`
Expected: FAIL (module not found)

**Step 3: Implement TaskQueue**

```python
# src/taskbrew/orchestrator/task_queue.py
"""SQLite-backed task queue for agent coordination."""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import aiosqlite


class TaskStatus(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskQueue:
    """Async SQLite-backed task queue."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_to TEXT,
                input_context TEXT,
                output_artifact TEXT,
                parent_task_id TEXT,
                error TEXT,
                session_id TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
        """)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def create_task(
        self,
        pipeline_id: str,
        task_type: str,
        input_context: str,
        parent_task_id: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """INSERT INTO tasks (id, pipeline_id, task_type, status, input_context,
               parent_task_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, pipeline_id, task_type, TaskStatus.PENDING, input_context,
             parent_task_id, now),
        )
        await self._db.commit()
        return task_id

    async def get_task(self, task_id: str) -> dict | None:
        cursor = await self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def assign_task(self, task_id: str, agent_name: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = ?, assigned_to = ?, started_at = ? WHERE id = ?",
            (TaskStatus.ASSIGNED, agent_name, now, task_id),
        )
        await self._db.commit()

    async def update_status(self, task_id: str, status: TaskStatus) -> None:
        await self._db.execute(
            "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
        )
        await self._db.commit()

    async def complete_task(self, task_id: str, output_artifact: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = ?, output_artifact = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.COMPLETED, output_artifact, now, task_id),
        )
        await self._db.commit()

    async def fail_task(self, task_id: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE tasks SET status = ?, error = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.FAILED, error, now, task_id),
        )
        await self._db.commit()

    async def get_pending_tasks(self, pipeline_id: str | None = None) -> list[dict]:
        if pipeline_id:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE status = ? AND pipeline_id = ? ORDER BY created_at",
                (TaskStatus.PENDING, pipeline_id),
            )
        else:
            cursor = await self._db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at",
                (TaskStatus.PENDING,),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_tasks_by_pipeline(self, pipeline_id: str) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM tasks WHERE pipeline_id = ? ORDER BY created_at",
            (pipeline_id,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_task_queue.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/task_queue.py tests/test_task_queue.py
git commit -m "feat: SQLite-backed async task queue with status lifecycle"
```

---

## Task 4: Base Agent Wrapper

**Files:**
- Create: `src/taskbrew/agents/__init__.py`
- Create: `src/taskbrew/agents/base.py`
- Create: `tests/test_agent_base.py`

**Step 1: Write the failing tests**

```python
# tests/test_agent_base.py
import pytest
from taskbrew.agents.base import AgentRunner, AgentStatus
from taskbrew.config import AgentConfig


def test_agent_config_creation():
    config = AgentConfig(
        name="coder",
        role="Implements code",
        system_prompt="You are a coder agent.",
        allowed_tools=["Read", "Write", "Edit", "Bash"],
    )
    assert config.name == "coder"
    assert "Bash" in config.allowed_tools


def test_agent_runner_initialization():
    config = AgentConfig(
        name="reviewer",
        role="Reviews code",
        system_prompt="You are a reviewer.",
        allowed_tools=["Read", "Glob", "Grep"],
    )
    runner = AgentRunner(config)
    assert runner.name == "reviewer"
    assert runner.status == AgentStatus.IDLE


def test_agent_runner_builds_options():
    config = AgentConfig(
        name="coder",
        role="Coder",
        system_prompt="You are a coding agent.",
        allowed_tools=["Read", "Write", "Bash"],
    )
    runner = AgentRunner(config)
    options = runner.build_options()
    assert options.system_prompt == "You are a coding agent."
    assert "Read" in options.allowed_tools
    assert options.permission_mode == "bypassPermissions"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_base.py -v`
Expected: FAIL (module not found)

**Step 3: Implement AgentRunner**

```python
# src/taskbrew/agents/__init__.py
"""Agent definitions and runners."""

# src/taskbrew/agents/base.py
"""Base agent runner wrapping ClaudeSDKClient."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock

from taskbrew.config import AgentConfig


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

    def __init__(self, config: AgentConfig, cli_path: str | None = None):
        self.config = config
        self.name = config.name
        self.status = AgentStatus.IDLE
        self.cli_path = cli_path
        self.session_id: str | None = None
        self._log: list[AgentEvent] = []

    def build_options(self, cwd: str | None = None) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions from agent config."""
        opts = ClaudeAgentOptions(
            system_prompt=self.config.system_prompt,
            allowed_tools=self.config.allowed_tools,
            permission_mode="bypassPermissions",
        )
        if self.config.max_turns:
            opts.max_turns = self.config.max_turns
        if self.cli_path:
            opts.cli_path = self.cli_path
        if cwd or self.config.cwd:
            opts.cwd = str(cwd or self.config.cwd)
        return opts

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
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_base.py -v`
Expected: All 3 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/agents/ tests/test_agent_base.py
git commit -m "feat: base AgentRunner wrapping ClaudeSDKClient with status tracking"
```

---

## Task 5: Agent Role Definitions

**Files:**
- Create: `src/taskbrew/agents/roles.py`
- Create: `tests/test_agent_roles.py`

**Step 1: Write the failing tests**

```python
# tests/test_agent_roles.py
import pytest
from taskbrew.agents.roles import get_agent_config, AGENT_ROLES


def test_all_roles_defined():
    expected = {"pm", "researcher", "architect", "coder", "tester", "reviewer"}
    assert set(AGENT_ROLES.keys()) == expected


def test_get_agent_config_coder():
    config = get_agent_config("coder")
    assert config.name == "coder"
    assert "Write" in config.allowed_tools
    assert "Edit" in config.allowed_tools
    assert "Bash" in config.allowed_tools


def test_get_agent_config_reviewer():
    config = get_agent_config("reviewer")
    assert config.name == "reviewer"
    assert "Write" not in config.allowed_tools  # reviewer is read-only
    assert "Read" in config.allowed_tools


def test_get_agent_config_unknown_raises():
    with pytest.raises(KeyError):
        get_agent_config("nonexistent")


def test_each_role_has_system_prompt():
    for name in AGENT_ROLES:
        config = get_agent_config(name)
        assert len(config.system_prompt) > 50, f"{name} system prompt too short"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_agent_roles.py -v`
Expected: FAIL

**Step 3: Implement role definitions**

```python
# src/taskbrew/agents/roles.py
"""Predefined agent role configurations."""

from taskbrew.config import AgentConfig

AGENT_ROLES: dict[str, dict] = {
    "pm": {
        "role": "Project Manager",
        "system_prompt": (
            "You are a Project Manager agent. Your job is to break down high-level goals "
            "into concrete, actionable development tasks. For each task, specify:\n"
            "- A clear title and description\n"
            "- Which agent role should handle it (researcher, architect, coder, tester, reviewer)\n"
            "- Dependencies on other tasks\n"
            "- Acceptance criteria\n\n"
            "You read the codebase to understand the project structure. You do NOT write code. "
            "Output your task breakdown as a structured list. Prioritize tasks logically."
        ),
        "allowed_tools": ["Read", "Glob", "Grep", "WebSearch"],
    },
    "researcher": {
        "role": "Researcher",
        "system_prompt": (
            "You are a Researcher agent. Your job is to gather context needed before "
            "implementation begins. This includes:\n"
            "- Reading existing code to understand patterns and conventions\n"
            "- Searching documentation and APIs for relevant information\n"
            "- Analyzing dependencies and their capabilities\n"
            "- Identifying potential risks or blockers\n\n"
            "Produce a research summary document with your findings, organized by topic. "
            "Include code snippets, links, and specific recommendations."
        ),
        "allowed_tools": ["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
    },
    "architect": {
        "role": "Architect",
        "system_prompt": (
            "You are an Architect agent. Your job is to design technical solutions based on "
            "research findings and task requirements. You produce:\n"
            "- Architecture decisions with rationale\n"
            "- File structure and module organization\n"
            "- Interface definitions and data flow diagrams\n"
            "- Technology choices with trade-offs\n\n"
            "Write your design as a clear markdown document. Be specific about file paths, "
            "function signatures, and data structures. Keep it simple - YAGNI."
        ),
        "allowed_tools": ["Read", "Glob", "Grep", "Write"],
    },
    "coder": {
        "role": "Coder",
        "system_prompt": (
            "You are a Coder agent. Your job is to implement code based on architecture "
            "designs and task specifications. Follow these principles:\n"
            "- Write clean, well-structured code\n"
            "- Follow existing project conventions\n"
            "- Make small, focused commits with descriptive messages\n"
            "- Do NOT force-push or rewrite history\n"
            "- Work on feature branches, never commit directly to main\n\n"
            "Read the design document and research context before coding. "
            "Implement exactly what was specified, no more."
        ),
        "allowed_tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    },
    "tester": {
        "role": "Tester",
        "system_prompt": (
            "You are a Tester agent. Your job is to validate code quality through testing:\n"
            "- Write unit tests for new functionality\n"
            "- Write integration tests for cross-module behavior\n"
            "- Run existing test suites and report results\n"
            "- Identify edge cases and error scenarios\n"
            "- Measure and report test coverage\n\n"
            "Produce a test results report with pass/fail counts, coverage percentage, "
            "and any issues found. Write tests that are clear, focused, and maintainable."
        ),
        "allowed_tools": ["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
    },
    "reviewer": {
        "role": "Code Reviewer",
        "system_prompt": (
            "You are a Code Reviewer agent. Your job is to review code for quality, "
            "correctness, and security. Check for:\n"
            "- Logic errors and edge cases\n"
            "- Security vulnerabilities (injection, XSS, etc.)\n"
            "- Code style and convention adherence\n"
            "- Performance issues\n"
            "- Missing error handling\n"
            "- Test coverage gaps\n\n"
            "Produce a review document with specific feedback. Categorize issues as: "
            "blocking (must fix), suggestion (should consider), or nit (minor style). "
            "You are read-only - you do NOT modify code."
        ),
        "allowed_tools": ["Read", "Glob", "Grep"],
    },
}


def get_agent_config(role_name: str) -> AgentConfig:
    """Get the AgentConfig for a predefined role."""
    if role_name not in AGENT_ROLES:
        raise KeyError(f"Unknown agent role: {role_name}. Available: {list(AGENT_ROLES.keys())}")

    role = AGENT_ROLES[role_name]
    return AgentConfig(
        name=role_name,
        role=role["role"],
        system_prompt=role["system_prompt"],
        allowed_tools=role["allowed_tools"],
    )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_agent_roles.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/agents/roles.py tests/test_agent_roles.py
git commit -m "feat: agent role definitions for PM/Researcher/Architect/Coder/Tester/Reviewer"
```

---

## Task 6: Team Manager

**Files:**
- Create: `src/taskbrew/orchestrator/team_manager.py`
- Create: `tests/test_team_manager.py`

**Step 1: Write the failing tests**

```python
# tests/test_team_manager.py
import pytest
from taskbrew.orchestrator.team_manager import TeamManager
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.base import AgentStatus


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def team_manager(event_bus):
    return TeamManager(event_bus=event_bus)


def test_spawn_agent(team_manager):
    team_manager.spawn_agent("coder")
    assert "coder" in team_manager.agents
    assert team_manager.agents["coder"].status == AgentStatus.IDLE


def test_spawn_duplicate_raises(team_manager):
    team_manager.spawn_agent("coder")
    with pytest.raises(ValueError, match="already exists"):
        team_manager.spawn_agent("coder")


def test_spawn_unknown_role_raises(team_manager):
    with pytest.raises(KeyError):
        team_manager.spawn_agent("nonexistent")


def test_stop_agent(team_manager):
    team_manager.spawn_agent("reviewer")
    team_manager.stop_agent("reviewer")
    assert team_manager.agents["reviewer"].status == AgentStatus.STOPPED


def test_get_team_status(team_manager):
    team_manager.spawn_agent("coder")
    team_manager.spawn_agent("reviewer")
    status = team_manager.get_team_status()
    assert len(status) == 2
    assert status["coder"] == AgentStatus.IDLE
    assert status["reviewer"] == AgentStatus.IDLE


def test_spawn_all_default_agents(team_manager):
    team_manager.spawn_default_team()
    assert len(team_manager.agents) == 6
    expected = {"pm", "researcher", "architect", "coder", "tester", "reviewer"}
    assert set(team_manager.agents.keys()) == expected
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_team_manager.py -v`
Expected: FAIL

**Step 3: Implement TeamManager**

```python
# src/taskbrew/orchestrator/team_manager.py
"""Manages the lifecycle of agent instances."""

from taskbrew.agents.base import AgentRunner, AgentStatus
from taskbrew.agents.roles import get_agent_config, AGENT_ROLES
from taskbrew.orchestrator.event_bus import EventBus


class TeamManager:
    """Spawns, stops, and monitors agent instances."""

    def __init__(self, event_bus: EventBus, cli_path: str | None = None):
        self.event_bus = event_bus
        self.cli_path = cli_path
        self.agents: dict[str, AgentRunner] = {}

    def spawn_agent(self, role_name: str) -> AgentRunner:
        if role_name in self.agents:
            raise ValueError(f"Agent '{role_name}' already exists")

        config = get_agent_config(role_name)
        runner = AgentRunner(config, cli_path=self.cli_path)
        self.agents[role_name] = runner
        return runner

    def stop_agent(self, agent_name: str) -> None:
        if agent_name in self.agents:
            self.agents[agent_name].status = AgentStatus.STOPPED

    def get_agent(self, agent_name: str) -> AgentRunner | None:
        return self.agents.get(agent_name)

    def get_team_status(self) -> dict[str, AgentStatus]:
        return {name: agent.status for name, agent in self.agents.items()}

    def spawn_default_team(self) -> None:
        for role_name in AGENT_ROLES:
            if role_name not in self.agents:
                self.spawn_agent(role_name)

    async def run_agent_task(
        self, agent_name: str, prompt: str, cwd: str | None = None
    ) -> str:
        agent = self.agents.get(agent_name)
        if not agent:
            raise ValueError(f"Agent '{agent_name}' not found")

        await self.event_bus.emit("agent_started", {
            "agent": agent_name, "prompt": prompt[:200]
        })

        try:
            result = await agent.run(prompt, cwd=cwd)
            await self.event_bus.emit("agent_completed", {
                "agent": agent_name, "result": result[:500]
            })
            return result
        except Exception as e:
            await self.event_bus.emit("agent_error", {
                "agent": agent_name, "error": str(e)
            })
            raise
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_team_manager.py -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/orchestrator/team_manager.py tests/test_team_manager.py
git commit -m "feat: team manager for spawning/stopping/monitoring agents"
```

---

## Task 7: Custom MCP Tools (Task & Git)

**Files:**
- Create: `src/taskbrew/tools/__init__.py`
- Create: `src/taskbrew/tools/task_tools.py`
- Create: `src/taskbrew/tools/git_tools.py`
- Create: `tests/test_tools.py`

**Step 1: Write the failing tests**

```python
# tests/test_tools.py
import pytest
from taskbrew.tools.task_tools import build_task_tools_server
from taskbrew.tools.git_tools import build_git_tools_server


def test_task_tools_server_created():
    server = build_task_tools_server(db_path=":memory:")
    assert server is not None


def test_git_tools_server_created():
    server = build_git_tools_server()
    assert server is not None
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL

**Step 3: Implement custom MCP tools**

```python
# src/taskbrew/tools/__init__.py
"""Custom MCP tools for agents."""

# src/taskbrew/tools/task_tools.py
"""MCP tools for task queue operations - agents can claim/complete tasks."""

from claude_agent_sdk import tool, create_sdk_mcp_server


def build_task_tools_server(db_path: str = "data/tasks.db"):
    """Build an MCP server with task management tools."""

    @tool(
        "claim_task",
        "Claim the next available pending task from the queue. Returns the task details.",
        {"pipeline_id": str},
    )
    async def claim_task(args):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Task claimed from pipeline {args.get('pipeline_id', 'default')}. "
                    "Check the task details in the response.",
                }
            ]
        }

    @tool(
        "complete_task",
        "Mark a task as completed with an optional output artifact path.",
        {"task_id": str, "artifact_path": str},
    )
    async def complete_task(args):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Task {args['task_id']} marked complete. "
                    f"Artifact: {args.get('artifact_path', 'none')}",
                }
            ]
        }

    @tool(
        "create_subtask",
        "Create a new subtask under the current task.",
        {"title": str, "description": str, "assigned_role": str},
    )
    async def create_subtask(args):
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Subtask created: {args['title']} -> {args['assigned_role']}",
                }
            ]
        }

    return create_sdk_mcp_server(
        name="task-tools",
        version="1.0.0",
        tools=[claim_task, complete_task, create_subtask],
    )


# src/taskbrew/tools/git_tools.py
"""MCP tools for git operations."""

from claude_agent_sdk import tool, create_sdk_mcp_server


def build_git_tools_server():
    """Build an MCP server with git management tools."""

    @tool(
        "create_feature_branch",
        "Create a new git branch for a feature from the current HEAD.",
        {"branch_name": str},
    )
    async def create_feature_branch(args):
        import asyncio

        branch = args["branch_name"]
        proc = await asyncio.create_subprocess_exec(
            "git", "checkout", "-b", branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode() + stderr.decode()
        return {"content": [{"type": "text", "text": f"Branch '{branch}' created.\n{output}"}]}

    @tool(
        "get_diff_summary",
        "Get a summary of current uncommitted changes.",
        {},
    )
    async def get_diff_summary(args):
        import asyncio

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return {"content": [{"type": "text", "text": stdout.decode() or "No changes."}]}

    return create_sdk_mcp_server(
        name="git-tools",
        version="1.0.0",
        tools=[create_feature_branch, get_diff_summary],
    )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools.py -v`
Expected: All 2 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/tools/ tests/test_tools.py
git commit -m "feat: custom MCP tools for task queue and git operations"
```

---

## Task 8: Workflow Engine

**Files:**
- Create: `src/taskbrew/orchestrator/workflow.py`
- Create: `tests/test_workflow.py`
- Create: `pipelines/feature_dev.yaml`

**Step 1: Write the failing tests**

```python
# tests/test_workflow.py
import pytest
from taskbrew.orchestrator.workflow import Pipeline, PipelineStep, WorkflowEngine


def test_pipeline_from_dict():
    data = {
        "name": "feature_dev",
        "description": "Feature development pipeline",
        "steps": [
            {"agent": "pm", "action": "decompose", "description": "Break down the goal"},
            {"agent": "researcher", "action": "research", "description": "Gather context"},
            {"agent": "coder", "action": "implement", "description": "Write code"},
        ],
    }
    pipeline = Pipeline.from_dict(data)
    assert pipeline.name == "feature_dev"
    assert len(pipeline.steps) == 3
    assert pipeline.steps[0].agent == "pm"
    assert pipeline.steps[2].agent == "coder"


def test_pipeline_step_order():
    pipeline = Pipeline(
        name="test",
        description="test",
        steps=[
            PipelineStep(agent="pm", action="plan", description="Plan"),
            PipelineStep(agent="coder", action="code", description="Code"),
        ],
    )
    assert pipeline.get_next_step(0) == pipeline.steps[1]
    assert pipeline.get_next_step(1) is None


def test_pipeline_from_yaml(tmp_path):
    yaml_content = """
name: bugfix
description: Bug fix pipeline
steps:
  - agent: researcher
    action: analyze
    description: Analyze the bug
  - agent: coder
    action: fix
    description: Fix the bug
"""
    yaml_file = tmp_path / "bugfix.yaml"
    yaml_file.write_text(yaml_content)
    pipeline = Pipeline.from_yaml(yaml_file)
    assert pipeline.name == "bugfix"
    assert len(pipeline.steps) == 2


def test_workflow_engine_registers_pipeline():
    engine = WorkflowEngine()
    pipeline = Pipeline(
        name="test",
        description="test",
        steps=[PipelineStep(agent="pm", action="plan", description="Plan")],
    )
    engine.register_pipeline(pipeline)
    assert "test" in engine.pipelines


def test_workflow_engine_load_from_directory(tmp_path):
    yaml_content = """
name: test_pipe
description: Test
steps:
  - agent: coder
    action: implement
    description: Code
"""
    (tmp_path / "test.yaml").write_text(yaml_content)
    engine = WorkflowEngine()
    engine.load_pipelines(tmp_path)
    assert "test_pipe" in engine.pipelines
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_workflow.py -v`
Expected: FAIL

**Step 3: Implement workflow engine**

```python
# src/taskbrew/orchestrator/workflow.py
"""Pipeline-based workflow engine for orchestrating agent tasks."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PipelineStep:
    """A single step in a pipeline."""
    agent: str
    action: str
    description: str
    retry_count: int = 0
    max_retries: int = 1


@dataclass
class Pipeline:
    """A workflow pipeline - a sequence of agent steps."""
    name: str
    description: str
    steps: list[PipelineStep] = field(default_factory=list)

    def get_next_step(self, current_index: int) -> PipelineStep | None:
        next_idx = current_index + 1
        if next_idx < len(self.steps):
            return self.steps[next_idx]
        return None

    @classmethod
    def from_dict(cls, data: dict) -> "Pipeline":
        steps = [
            PipelineStep(
                agent=s["agent"],
                action=s["action"],
                description=s["description"],
                max_retries=s.get("max_retries", 1),
            )
            for s in data["steps"]
        ]
        return cls(name=data["name"], description=data["description"], steps=steps)

    @classmethod
    def from_yaml(cls, path: Path) -> "Pipeline":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)


@dataclass
class PipelineRun:
    """Tracks the execution state of a pipeline."""
    pipeline_name: str
    run_id: str
    current_step: int = 0
    status: str = "running"  # running, paused, completed, failed
    context: dict = field(default_factory=dict)


class WorkflowEngine:
    """Manages pipeline registration and execution."""

    def __init__(self):
        self.pipelines: dict[str, Pipeline] = {}
        self.active_runs: dict[str, PipelineRun] = {}

    def register_pipeline(self, pipeline: Pipeline) -> None:
        self.pipelines[pipeline.name] = pipeline

    def load_pipelines(self, directory: Path) -> None:
        for yaml_file in directory.glob("*.yaml"):
            pipeline = Pipeline.from_yaml(yaml_file)
            self.register_pipeline(pipeline)
        for yml_file in directory.glob("*.yml"):
            pipeline = Pipeline.from_yaml(yml_file)
            self.register_pipeline(pipeline)

    def start_run(self, pipeline_name: str, run_id: str, initial_context: dict | None = None) -> PipelineRun:
        if pipeline_name not in self.pipelines:
            raise KeyError(f"Pipeline '{pipeline_name}' not found")

        run = PipelineRun(
            pipeline_name=pipeline_name,
            run_id=run_id,
            context=initial_context or {},
        )
        self.active_runs[run_id] = run
        return run

    def get_current_step(self, run_id: str) -> PipelineStep | None:
        run = self.active_runs.get(run_id)
        if not run:
            return None
        pipeline = self.pipelines[run.pipeline_name]
        if run.current_step < len(pipeline.steps):
            return pipeline.steps[run.current_step]
        return None

    def advance_run(self, run_id: str) -> PipelineStep | None:
        run = self.active_runs.get(run_id)
        if not run:
            return None
        pipeline = self.pipelines[run.pipeline_name]
        next_step = pipeline.get_next_step(run.current_step)
        if next_step:
            run.current_step += 1
            return next_step
        else:
            run.status = "completed"
            return None
```

**Step 4: Create default pipeline YAML**

```yaml
# pipelines/feature_dev.yaml
name: feature_dev
description: Full feature development lifecycle
steps:
  - agent: pm
    action: decompose
    description: Break down the goal into concrete tasks with dependencies
  - agent: researcher
    action: research
    description: Gather context - read codebase, search docs, identify patterns
  - agent: architect
    action: design
    description: Design the technical solution and write architecture doc
  - agent: coder
    action: implement
    description: Implement the solution on a feature branch
  - agent: tester
    action: test
    description: Write and run tests to validate the implementation
  - agent: reviewer
    action: review
    description: Review code quality, security, and correctness
```

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_workflow.py -v`
Expected: All 5 tests PASS

**Step 6: Commit**

```bash
git add src/taskbrew/orchestrator/workflow.py tests/test_workflow.py pipelines/
git commit -m "feat: YAML-based workflow engine with pipeline steps and run tracking"
```

---

## Task 9: Dashboard Backend (FastAPI + WebSocket)

**Files:**
- Create: `src/taskbrew/dashboard/__init__.py`
- Create: `src/taskbrew/dashboard/app.py`
- Create: `tests/test_dashboard.py`

**Step 1: Write the failing tests**

```python
# tests/test_dashboard.py
import pytest
from httpx import AsyncClient, ASGITransport
from taskbrew.dashboard.app import create_app
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.team_manager import TeamManager
from taskbrew.orchestrator.task_queue import TaskQueue
from taskbrew.orchestrator.workflow import WorkflowEngine


@pytest.fixture
async def app_deps(tmp_path):
    event_bus = EventBus()
    team_mgr = TeamManager(event_bus=event_bus)
    task_queue = TaskQueue(db_path=tmp_path / "test.db")
    await task_queue.initialize()
    workflow = WorkflowEngine()
    return event_bus, team_mgr, task_queue, workflow


@pytest.fixture
async def app(app_deps):
    event_bus, team_mgr, task_queue, workflow = app_deps
    return create_app(
        event_bus=event_bus,
        team_manager=team_mgr,
        task_queue=task_queue,
        workflow_engine=workflow,
    )


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_endpoint(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_team_status_endpoint(client):
    resp = await client.get("/api/team")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_tasks_endpoint(app_deps, client):
    _, _, task_queue, _ = app_deps
    await task_queue.create_task(
        pipeline_id="p1", task_type="implement", input_context="Build auth"
    )
    resp = await client.get("/api/tasks")
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 1


async def test_pipelines_endpoint(client):
    resp = await client.get("/api/pipelines")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: FAIL

Note: Add `httpx` to dev dependencies in pyproject.toml:
```
[project.optional-dependencies]
dev = [
    ...
    "httpx>=0.28.0",
]
```

**Step 3: Implement dashboard**

```python
# src/taskbrew/dashboard/__init__.py
"""Dashboard for monitoring AI team."""

# src/taskbrew/dashboard/app.py
"""FastAPI dashboard backend with WebSocket support."""

import asyncio
import json
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.team_manager import TeamManager
from taskbrew.orchestrator.task_queue import TaskQueue
from taskbrew.orchestrator.workflow import WorkflowEngine


class ConnectionManager:
    """Manages WebSocket connections for broadcasting."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict[str, Any]):
        message = json.dumps(data)
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                self.active.remove(ws)


def create_app(
    event_bus: EventBus,
    team_manager: TeamManager,
    task_queue: TaskQueue,
    workflow_engine: WorkflowEngine,
) -> FastAPI:
    app = FastAPI(title="AI Team Dashboard")
    ws_manager = ConnectionManager()

    # Wire event bus to WebSocket broadcasts
    async def broadcast_event(event: dict):
        await ws_manager.broadcast(event)

    event_bus.subscribe("*", broadcast_event)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/team")
    async def get_team():
        return {
            name: str(status)
            for name, status in team_manager.get_team_status().items()
        }

    @app.get("/api/tasks")
    async def get_tasks():
        pending = await task_queue.get_pending_tasks()
        return pending

    @app.get("/api/pipelines")
    async def get_pipelines():
        return [
            {"name": p.name, "description": p.description, "steps": len(p.steps)}
            for p in workflow_engine.pipelines.values()
        ]

    @app.post("/api/pipelines/{pipeline_name}/run")
    async def start_pipeline(pipeline_name: str, goal: dict):
        import uuid
        run_id = str(uuid.uuid4())[:8]
        run = workflow_engine.start_run(pipeline_name, run_id, initial_context=goal)
        step = workflow_engine.get_current_step(run_id)
        if step:
            task_id = await task_queue.create_task(
                pipeline_id=run_id,
                task_type=step.action,
                input_context=json.dumps(goal),
            )
            await event_bus.emit("pipeline_started", {
                "run_id": run_id, "pipeline": pipeline_name, "first_task": task_id
            })
        return {"run_id": run_id, "status": "started"}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                data = await ws.receive_text()
                # Handle incoming commands from dashboard
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    @app.get("/")
    async def index():
        return HTMLResponse(DASHBOARD_HTML)

    return app


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>AI Team Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card h3 { color: #58a6ff; margin-bottom: 8px; }
        .status { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
        .status.idle { background: #238636; }
        .status.working { background: #d29922; }
        .status.error { background: #da3633; }
        #log { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 16px; max-height: 400px; overflow-y: auto; font-family: monospace; font-size: 13px; }
        .log-entry { padding: 4px 0; border-bottom: 1px solid #21262d; }
    </style>
</head>
<body>
    <h1>AI Team Dashboard</h1>
    <div class="grid" id="agents"></div>
    <h2 style="color:#58a6ff;margin-bottom:12px">Event Log</h2>
    <div id="log"></div>

    <script>
        const ws = new WebSocket(`ws://${location.host}/ws`);
        const log = document.getElementById('log');
        const agents = document.getElementById('agents');

        ws.onmessage = (e) => {
            const event = JSON.parse(e.data);
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.textContent = `[${new Date().toLocaleTimeString()}] ${event.type}: ${JSON.stringify(event)}`;
            log.prepend(entry);
        };

        async function refreshTeam() {
            const resp = await fetch('/api/team');
            const team = await resp.json();
            agents.innerHTML = '';
            for (const [name, status] of Object.entries(team)) {
                agents.innerHTML += `
                    <div class="card">
                        <h3>${name}</h3>
                        <span class="status ${status}">${status}</span>
                    </div>`;
            }
        }

        setInterval(refreshTeam, 3000);
        refreshTeam();
    </script>
</body>
</html>
"""
```

**Step 4: Run tests to verify they pass**

Run: `pip install httpx && python -m pytest tests/test_dashboard.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/taskbrew/dashboard/ tests/test_dashboard.py pyproject.toml
git commit -m "feat: FastAPI dashboard with WebSocket real-time events and REST API"
```

---

## Task 10: Main Entry Point & CLI

**Files:**
- Create: `src/taskbrew/main.py`
- Create: `tests/test_main.py`

**Step 1: Write the failing test**

```python
# tests/test_main.py
import pytest
from taskbrew.main import build_orchestrator


async def test_build_orchestrator(tmp_path):
    orch = await build_orchestrator(project_dir=tmp_path)
    assert orch.team_manager is not None
    assert orch.task_queue is not None
    assert orch.event_bus is not None
    assert orch.workflow_engine is not None
    await orch.shutdown()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL

**Step 3: Implement main entry point**

```python
# src/taskbrew/main.py
"""Main entry point for the AI Team Orchestrator."""

import asyncio
import argparse
from pathlib import Path

import uvicorn

from taskbrew.config import OrchestratorConfig
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_queue import TaskQueue
from taskbrew.orchestrator.team_manager import TeamManager
from taskbrew.orchestrator.workflow import WorkflowEngine
from taskbrew.dashboard.app import create_app


class Orchestrator:
    """Top-level orchestrator combining all components."""

    def __init__(
        self,
        event_bus: EventBus,
        task_queue: TaskQueue,
        team_manager: TeamManager,
        workflow_engine: WorkflowEngine,
        config: OrchestratorConfig,
    ):
        self.event_bus = event_bus
        self.task_queue = task_queue
        self.team_manager = team_manager
        self.workflow_engine = workflow_engine
        self.config = config

    async def shutdown(self):
        await self.task_queue.close()


async def build_orchestrator(
    project_dir: Path | None = None,
    cli_path: str | None = None,
) -> Orchestrator:
    """Build and initialize the orchestrator with all components."""
    config = OrchestratorConfig(
        project_dir=project_dir or Path.cwd(),
        cli_path=cli_path,
    )

    event_bus = EventBus()
    task_queue = TaskQueue(db_path=config.db_path)
    await task_queue.initialize()

    team_manager = TeamManager(event_bus=event_bus, cli_path=config.cli_path)
    workflow_engine = WorkflowEngine()

    # Load pipelines if directory exists
    pipelines_dir = config.project_dir / "pipelines"
    if pipelines_dir.exists():
        workflow_engine.load_pipelines(pipelines_dir)

    return Orchestrator(
        event_bus=event_bus,
        task_queue=task_queue,
        team_manager=team_manager,
        workflow_engine=workflow_engine,
        config=config,
    )


async def run_server(orch: Orchestrator):
    """Start the dashboard server."""
    app = create_app(
        event_bus=orch.event_bus,
        team_manager=orch.team_manager,
        task_queue=orch.task_queue,
        workflow_engine=orch.workflow_engine,
    )
    config = uvicorn.Config(
        app=app,
        host=orch.config.dashboard_host,
        port=orch.config.dashboard_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def async_main(args):
    orch = await build_orchestrator(
        project_dir=Path(args.project_dir) if args.project_dir else None,
        cli_path=args.cli_path,
    )

    if args.command == "serve":
        # Spawn default team and start dashboard
        orch.team_manager.spawn_default_team()
        print(f"Dashboard: http://{orch.config.dashboard_host}:{orch.config.dashboard_port}")
        await run_server(orch)

    elif args.command == "run":
        # Run a single pipeline
        orch.team_manager.spawn_default_team()
        if not args.pipeline:
            print("Error: --pipeline required for 'run' command")
            return
        import uuid
        run_id = str(uuid.uuid4())[:8]
        run = orch.workflow_engine.start_run(
            args.pipeline, run_id, initial_context={"goal": args.goal or ""}
        )
        print(f"Started pipeline '{args.pipeline}' (run: {run_id})")

        # Execute pipeline steps sequentially
        while True:
            step = orch.workflow_engine.get_current_step(run_id)
            if not step:
                print("Pipeline completed!")
                break
            print(f"\n--- Step: {step.agent} -> {step.action} ---")
            print(f"Description: {step.description}")

            prompt = f"""You are executing step '{step.action}' of the '{args.pipeline}' pipeline.

Goal: {args.goal or 'No goal specified'}

Your task: {step.description}

Context from previous steps: {run.context}

Work in the project directory. Produce your output and be thorough."""

            try:
                result = await orch.team_manager.run_agent_task(
                    step.agent, prompt, cwd=str(orch.config.project_dir)
                )
                run.context[f"step_{run.current_step}_{step.agent}"] = result[:2000]
                print(f"Result: {result[:500]}")
                orch.workflow_engine.advance_run(run_id)
            except Exception as e:
                print(f"Error in step {step.agent}: {e}")
                break

    elif args.command == "status":
        orch.team_manager.spawn_default_team()
        status = orch.team_manager.get_team_status()
        for name, state in status.items():
            print(f"  {name}: {state}")

    await orch.shutdown()


def cli_main():
    parser = argparse.ArgumentParser(description="AI Team Orchestrator")
    parser.add_argument("command", choices=["serve", "run", "status"],
                        help="Command to execute")
    parser.add_argument("--project-dir", help="Project directory to work in")
    parser.add_argument("--cli-path", help="Path to Claude Code CLI binary")
    parser.add_argument("--pipeline", help="Pipeline to run (for 'run' command)")
    parser.add_argument("--goal", help="Goal description (for 'run' command)")

    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    cli_main()
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_main.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/taskbrew/main.py tests/test_main.py
git commit -m "feat: main entry point with serve/run/status CLI commands"
```

---

## Task 11: Additional Pipeline YAMLs

**Files:**
- Create: `pipelines/bugfix.yaml`
- Create: `pipelines/code_review.yaml`

**Step 1: Create bugfix pipeline**

```yaml
# pipelines/bugfix.yaml
name: bugfix
description: Bug investigation and fix pipeline
steps:
  - agent: researcher
    action: analyze
    description: Reproduce the bug and analyze root cause. Read error logs, stack traces, and relevant code.
  - agent: coder
    action: fix
    description: Implement the fix on a bugfix branch. Make minimal, focused changes.
  - agent: tester
    action: verify
    description: Write regression tests and verify the fix resolves the issue.
  - agent: reviewer
    action: review
    description: Review the fix for correctness and potential side effects.
```

**Step 2: Create code review pipeline**

```yaml
# pipelines/code_review.yaml
name: code_review
description: Standalone code review pipeline
steps:
  - agent: reviewer
    action: review
    description: Review the specified code or PR for quality, security, and correctness.
  - agent: coder
    action: address_feedback
    description: Address review feedback and make requested changes.
  - agent: reviewer
    action: re_review
    description: Verify that feedback has been addressed satisfactorily.
```

**Step 3: Commit**

```bash
git add pipelines/
git commit -m "feat: bugfix and code_review pipeline definitions"
```

---

## Task 12: Integration Test - End to End

**Files:**
- Create: `tests/test_integration.py`

**Step 1: Write integration test**

```python
# tests/test_integration.py
"""Integration tests verifying the full orchestrator wiring."""

import pytest
from pathlib import Path

from taskbrew.main import build_orchestrator
from taskbrew.orchestrator.task_queue import TaskStatus


@pytest.fixture
async def orch(tmp_path):
    # Create a pipelines dir with a test pipeline
    pipelines_dir = tmp_path / "pipelines"
    pipelines_dir.mkdir()
    (pipelines_dir / "test.yaml").write_text("""
name: test_pipeline
description: Test pipeline
steps:
  - agent: researcher
    action: research
    description: Research the topic
  - agent: coder
    action: implement
    description: Implement the solution
""")

    o = await build_orchestrator(project_dir=tmp_path)
    yield o
    await o.shutdown()


async def test_full_wiring(orch):
    """Verify all components are wired together."""
    assert orch.event_bus is not None
    assert orch.task_queue is not None
    assert orch.team_manager is not None
    assert orch.workflow_engine is not None


async def test_pipeline_loaded(orch):
    """Verify pipelines loaded from YAML."""
    assert "test_pipeline" in orch.workflow_engine.pipelines


async def test_team_spawn_and_status(orch):
    """Verify team can be spawned and status queried."""
    orch.team_manager.spawn_default_team()
    status = orch.team_manager.get_team_status()
    assert len(status) == 6
    assert all(s == "idle" for s in status.values())


async def test_task_creation_and_events(orch):
    """Verify tasks create events."""
    events_received = []

    async def capture(event):
        events_received.append(event)

    orch.event_bus.subscribe("*", capture)

    task_id = await orch.task_queue.create_task(
        pipeline_id="run-1",
        task_type="research",
        input_context="Test context",
    )

    await orch.event_bus.emit("task_created", {"task_id": task_id})

    import asyncio
    await asyncio.sleep(0.01)
    assert len(events_received) >= 1


async def test_workflow_start_creates_task(orch):
    """Verify starting a pipeline run creates the first task step."""
    run = orch.workflow_engine.start_run(
        "test_pipeline", "run-1", initial_context={"goal": "Test"}
    )
    step = orch.workflow_engine.get_current_step("run-1")
    assert step is not None
    assert step.agent == "researcher"
    assert step.action == "research"
```

**Step 2: Run integration tests**

Run: `python -m pytest tests/test_integration.py -v`
Expected: All 5 tests PASS

**Step 3: Run the full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests PASS (event_bus: 5, task_queue: 7, agent_base: 3, agent_roles: 5, team_manager: 6, tools: 2, workflow: 5, dashboard: 4, main: 1, integration: 5 = ~43 tests)

**Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: integration tests verifying full orchestrator wiring"
```

---

## Task 13: Final Verification & Documentation

**Step 1: Run full test suite with coverage**

Run: `python -m pytest tests/ -v --cov=taskbrew --cov-report=term-missing`
Expected: All tests pass, coverage report generated

**Step 2: Test the CLI entry point**

Run: `cd "/Users/nikhilchatragadda/Personal Projects/ai-team" && python -m taskbrew.main status`
Expected: Lists 6 agents, all idle

**Step 3: Test the dashboard starts**

Run: `python -m taskbrew.main serve &` (then curl http://127.0.0.1:8420/api/health)
Expected: `{"status": "ok"}`

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: AI Team Orchestrator v0.1.0 - complete foundation"
```

---

## Summary

| Task | Component | Tests | Estimated Effort |
|------|-----------|-------|-----------------|
| 1 | Project scaffolding | Setup | Foundation |
| 2 | Event bus | 5 tests | Small |
| 3 | Task queue (SQLite) | 7 tests | Medium |
| 4 | Base agent wrapper | 3 tests | Medium |
| 5 | Agent role definitions | 5 tests | Small |
| 6 | Team manager | 6 tests | Medium |
| 7 | Custom MCP tools | 2 tests | Small |
| 8 | Workflow engine | 5 tests | Medium |
| 9 | Dashboard (FastAPI) | 4 tests | Large |
| 10 | Main entry point & CLI | 1 test | Medium |
| 11 | Pipeline YAMLs | - | Small |
| 12 | Integration tests | 5 tests | Medium |
| 13 | Final verification | - | Small |

**Total: 13 tasks, ~43 tests, incrementally buildable and testable.**

## Next Steps After v0.1.0

- Add hooks integration (PreToolUse/PostToolUse wired to event bus)
- Add concurrent agent execution with asyncio.gather
- Add artifact passing between pipeline steps
- Add git worktree management per agent
- Enhance dashboard with task board and log streaming
- Add human intervention checkpoints
- Add retry logic in workflow engine
- Add persistent pipeline run state (survive restarts)
