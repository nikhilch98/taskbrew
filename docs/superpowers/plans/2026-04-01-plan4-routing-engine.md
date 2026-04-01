# Plan 4: Routing Engine & Execution Policies

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire together the configuration, context, and provider layers that connect presets (Plan 1), pipeline topology (Plan 2), and MCP tools (Plan 3) into a working execution pipeline. Specifically: parse the `execution` config section from `team.yaml` with safe defaults, build the injected system prompt from task + pipeline + context_includes per the spec's section 4.1 template, resolve CLI providers from model names, and verify end-to-end with an integration test.

**Architecture:** A new `ExecutionConfig` dataclass in `config_loader.py` holds orchestrator-level settings (`max_concurrent_api_calls`, `base_branch`, `worktree_retention_days`, `max_pipeline_depth`). The `TeamConfig` gains an `execution` field. A new `system_prompt_builder.py` module produces the spec section 4.1 system prompt injection by combining task metadata, parent/root artifacts, sibling summaries, rejection history, and connected agents from the pipeline topology. The existing `ProviderRegistry` in `provider.py` already handles model-to-CLI mapping; this plan adds a convenience function (`resolve_cli_provider`) and tests to verify the full preset-to-prompt path.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, pytest, asyncio

**Spec Reference:** `docs/superpowers/specs/2026-04-01-agent-presets-pipeline-editor-design.md` sections 4 (Hybrid Routing Engine), 5 (Execution Policies), 8 (Execution Configuration)

---

## File Structure

### New Files
- `src/taskbrew/orchestrator/system_prompt_builder.py` -- Builds the injected system prompt per spec section 4.1
- `tests/test_routing_engine.py` -- All tests for Plan 4

### Modified Files
- `src/taskbrew/config_loader.py` -- Add `ExecutionConfig` dataclass, add `execution` field to `TeamConfig`, update `load_team_config()`
- `src/taskbrew/agents/provider.py` -- Add `resolve_cli_provider()` convenience function

---

## Task 1: Execution Config Parsing

**Files:**
- Modify: `src/taskbrew/config_loader.py`
- Test: `tests/test_routing_engine.py` (create new)

- [ ] **Step 1: Write failing tests for ExecutionConfig**

```python
# tests/test_routing_engine.py
"""Tests for Plan 4: Routing Engine & Execution Policies."""

import asyncio
import textwrap
from pathlib import Path

import pytest
import yaml

from taskbrew.config_loader import (
    ExecutionConfig,
    TeamConfig,
    load_team_config,
)


class TestExecutionConfig:
    """Test parsing of the execution section in team.yaml."""

    def test_defaults_when_section_missing(self, tmp_path):
        """team.yaml with no execution section uses all defaults."""
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(textwrap.dedent("""\
            team_name: "Test Team"
            database:
              path: ":memory:"
            dashboard:
              host: "0.0.0.0"
              port: 8420
            artifacts:
              base_dir: "artifacts"
            defaults:
              max_instances: 1
              poll_interval_seconds: 5
              idle_timeout_minutes: 30
        """))
        cfg = load_team_config(team_yaml)
        assert cfg.execution.max_concurrent_api_calls == 5
        assert cfg.execution.base_branch == "main"
        assert cfg.execution.worktree_retention_days == 7
        assert cfg.execution.max_pipeline_depth == 20
        assert cfg.execution.artifact_exclude_patterns == [
            "*.env", "credentials*", "*.key", "*.pem", "*.secret",
        ]

    def test_explicit_values_parsed(self, tmp_path):
        """Explicit execution section values override defaults."""
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(textwrap.dedent("""\
            team_name: "Test Team"
            database:
              path: ":memory:"
            dashboard:
              host: "0.0.0.0"
              port: 8420
            artifacts:
              base_dir: "artifacts"
            defaults:
              max_instances: 1
              poll_interval_seconds: 5
              idle_timeout_minutes: 30
            execution:
              max_concurrent_api_calls: 10
              base_branch: "develop"
              worktree_retention_days: 14
              max_pipeline_depth: 50
              artifact_exclude_patterns:
                - "*.secret"
        """))
        cfg = load_team_config(team_yaml)
        assert cfg.execution.max_concurrent_api_calls == 10
        assert cfg.execution.base_branch == "develop"
        assert cfg.execution.worktree_retention_days == 14
        assert cfg.execution.max_pipeline_depth == 50
        assert cfg.execution.artifact_exclude_patterns == ["*.secret"]

    def test_partial_execution_section_fills_defaults(self, tmp_path):
        """Partial execution section fills missing keys with defaults."""
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(textwrap.dedent("""\
            team_name: "Test Team"
            database:
              path: ":memory:"
            dashboard:
              host: "0.0.0.0"
              port: 8420
            artifacts:
              base_dir: "artifacts"
            defaults:
              max_instances: 1
              poll_interval_seconds: 5
              idle_timeout_minutes: 30
            execution:
              base_branch: "release"
        """))
        cfg = load_team_config(team_yaml)
        assert cfg.execution.max_concurrent_api_calls == 5  # default
        assert cfg.execution.base_branch == "release"       # explicit
        assert cfg.execution.worktree_retention_days == 7    # default
        assert cfg.execution.max_pipeline_depth == 20        # default
```

- [ ] **Step 2: Implement ExecutionConfig dataclass and parsing**

Add to `src/taskbrew/config_loader.py`:

```python
@dataclass
class ExecutionConfig:
    """Orchestrator-level execution settings from team.yaml.

    All fields have sensible defaults — the ``execution`` section can be
    omitted entirely from team.yaml.
    """

    max_concurrent_api_calls: int = 5
    base_branch: str = "main"
    worktree_retention_days: int = 7
    max_pipeline_depth: int = 20
    artifact_exclude_patterns: list[str] = field(default_factory=lambda: [
        "*.env", "credentials*", "*.key", "*.pem", "*.secret",
    ])
```

Add `execution: ExecutionConfig` field to the `TeamConfig` dataclass (default `ExecutionConfig()`).

Update `load_team_config()` to parse the `execution` section:

```python
    # Parse execution config
    exec_raw = data.get("execution", {}) or {}
    default_excludes = ["*.env", "credentials*", "*.key", "*.pem", "*.secret"]
    execution = ExecutionConfig(
        max_concurrent_api_calls=exec_raw.get("max_concurrent_api_calls", 5),
        base_branch=exec_raw.get("base_branch", "main"),
        worktree_retention_days=exec_raw.get("worktree_retention_days", 7),
        max_pipeline_depth=exec_raw.get("max_pipeline_depth", 20),
        artifact_exclude_patterns=exec_raw.get(
            "artifact_exclude_patterns", default_excludes
        ),
    )
```

Pass `execution=execution` when constructing `TeamConfig`.

- [ ] **Step 3: Run tests — all three pass**

---

## Task 2: System Prompt Builder

**Files:**
- Create: `src/taskbrew/orchestrator/system_prompt_builder.py`
- Test: `tests/test_routing_engine.py`

This implements the spec section 4.1 system prompt injection template. The builder takes task metadata, pipeline topology, context data, and produces the full injected prompt string. It is a pure function (no database access) so it can be tested without any async infrastructure.

- [ ] **Step 1: Write failing tests for system prompt builder**

```python
# tests/test_routing_engine.py (append to existing file)

from taskbrew.orchestrator.system_prompt_builder import build_task_system_prompt
from taskbrew.config_loader import PipelineConfig, PipelineEdge


class TestSystemPromptBuilder:
    """Test the spec section 4.1 system prompt injection."""

    def test_minimal_prompt(self):
        """Minimal task produces required sections."""
        prompt = build_task_system_prompt(
            agent_role="coder_be",
            task={
                "id": "CB-001",
                "title": "Implement user login",
                "group_id": "FEAT-001",
                "priority": "high",
                "task_type": "implementation",
                "description": "Build the /api/login endpoint.",
            },
            pipeline=PipelineConfig(),
            context={},
        )
        assert "== TASK CONTEXT ==" in prompt
        assert "Agent Role: coder_be" in prompt
        assert "Task ID: CB-001" in prompt
        assert "Chain ID: N/A" in prompt
        assert "Title: Implement user login" in prompt
        assert "Group: FEAT-001" in prompt
        assert "Priority: high" in prompt
        assert "== DESCRIPTION ==" in prompt
        assert "Build the /api/login endpoint." in prompt
        assert "== CONNECTED AGENTS ==" in prompt

    def test_chain_id_included(self):
        """chain_id is shown when present on the task."""
        prompt = build_task_system_prompt(
            agent_role="coder_be",
            task={
                "id": "CB-002",
                "title": "Fix login bug",
                "group_id": "FEAT-001",
                "priority": "medium",
                "task_type": "revision",
                "description": "Fix the null check.",
                "chain_id": "CB-001",
            },
            pipeline=PipelineConfig(),
            context={},
        )
        assert "Chain ID: CB-001" in prompt

    def test_connected_agents_from_pipeline(self):
        """Connected agents are derived from outgoing pipeline edges."""
        pipeline = PipelineConfig(
            edges=[
                PipelineEdge(
                    id="e1",
                    from_agent="coder_be",
                    to_agent="architect_reviewer",
                    task_types=["verification"],
                ),
                PipelineEdge(
                    id="e2",
                    from_agent="pm",
                    to_agent="coder_be",
                    task_types=["implementation"],
                ),
            ],
        )
        prompt = build_task_system_prompt(
            agent_role="coder_be",
            task={
                "id": "CB-003",
                "title": "Build API",
                "group_id": "FEAT-002",
                "priority": "medium",
                "task_type": "implementation",
                "description": "Build it.",
            },
            pipeline=pipeline,
            context={},
        )
        assert "architect_reviewer" in prompt
        assert "verification" in prompt
        # Incoming edge from pm should NOT appear as a connected agent
        assert "- pm" not in prompt

    def test_context_sections_included(self):
        """Optional context sections appear when provided."""
        prompt = build_task_system_prompt(
            agent_role="coder_be",
            task={
                "id": "CB-004",
                "title": "Task",
                "group_id": "G-1",
                "priority": "low",
                "task_type": "implementation",
                "description": "Do the thing.",
            },
            pipeline=PipelineConfig(),
            context={
                "parent_artifact": "## Design Doc\nUse REST for /api/users.",
                "root_artifact": "Goal: Build a user management system.",
                "sibling_summary": "CB-003 completed: login endpoint done.",
                "rejection_history": "Attempt 1 rejected: missing validation.",
            },
        )
        assert "== PARENT ARTIFACT ==" in prompt
        assert "Use REST for /api/users." in prompt
        assert "== ROOT ARTIFACT ==" in prompt
        assert "Build a user management system." in prompt
        assert "== SIBLING SUMMARY ==" in prompt
        assert "login endpoint done." in prompt
        assert "== REJECTION HISTORY ==" in prompt
        assert "missing validation." in prompt

    def test_empty_context_shows_none(self):
        """When context sections are empty/missing, 'None' is shown."""
        prompt = build_task_system_prompt(
            agent_role="pm",
            task={
                "id": "PM-001",
                "title": "Decompose goal",
                "group_id": "G-1",
                "priority": "high",
                "task_type": "goal",
                "description": "Build user login.",
            },
            pipeline=PipelineConfig(),
            context={},
        )
        assert "== PARENT ARTIFACT ==\nNone" in prompt
        assert "== ROOT ARTIFACT ==\nNone" in prompt
        assert "== SIBLING SUMMARY ==\nNone" in prompt
        assert "== REJECTION HISTORY ==\nNone -- first attempt" in prompt
```

- [ ] **Step 2: Implement the system prompt builder**

Create `src/taskbrew/orchestrator/system_prompt_builder.py`:

```python
"""Build the injected system prompt for agent task execution.

Implements the spec section 4.1 template. This is a pure function with no
I/O -- all data is passed in as arguments so it can be unit-tested without
a database or running orchestrator.
"""

from __future__ import annotations

from taskbrew.config_loader import PipelineConfig


def build_task_system_prompt(
    *,
    agent_role: str,
    task: dict,
    pipeline: PipelineConfig,
    context: dict[str, str | None],
) -> str:
    """Build the full system prompt injection for a task.

    Parameters
    ----------
    agent_role:
        The role ID of the agent executing the task.
    task:
        Task dict with at least: id, title, group_id, priority, task_type,
        description.  Optional: chain_id.
    pipeline:
        The current pipeline config (used to derive connected agents).
    context:
        Optional context sections.  Keys: ``parent_artifact``,
        ``root_artifact``, ``sibling_summary``, ``rejection_history``.
        Missing or None values render as "None".

    Returns
    -------
    str
        The fully rendered system prompt injection string.
    """
    parts: list[str] = []

    # -- Task Context --
    chain_id = task.get("chain_id") or "N/A"
    parts.append("== TASK CONTEXT ==")
    parts.append(f"Agent Role: {agent_role}")
    parts.append(f"Task ID: {task['id']}")
    parts.append(f"Chain ID: {chain_id}")
    parts.append(f"Title: {task['title']}")
    parts.append(f"Group: {task['group_id']}")
    parts.append(f"Priority: {task['priority']}")

    # -- Description --
    parts.append("")
    parts.append("== DESCRIPTION ==")
    parts.append(task.get("description") or "None")

    # -- Parent Artifact --
    parts.append("")
    parts.append("== PARENT ARTIFACT ==")
    parts.append(context.get("parent_artifact") or "None")

    # -- Root Artifact --
    parts.append("")
    parts.append("== ROOT ARTIFACT ==")
    parts.append(context.get("root_artifact") or "None")

    # -- Sibling Summary --
    parts.append("")
    parts.append("== SIBLING SUMMARY ==")
    parts.append(context.get("sibling_summary") or "None")

    # -- Rejection History --
    parts.append("")
    parts.append("== REJECTION HISTORY ==")
    rejection = context.get("rejection_history")
    parts.append(rejection if rejection else "None -- first attempt")

    # -- Connected Agents (outgoing pipeline edges) --
    parts.append("")
    parts.append("== CONNECTED AGENTS ==")
    outgoing = [
        edge for edge in pipeline.edges
        if edge.from_agent == agent_role
    ]
    if outgoing:
        parts.append("You can route tasks to these agents:")
        for edge in outgoing:
            types_str = ", ".join(edge.task_types) if edge.task_types else "any"
            parts.append(f"- {edge.to_agent} (accepts: {types_str})")
    else:
        parts.append("No outgoing connections. You are a terminal node.")

    parts.append("")
    parts.append(
        "Use `route_task` to send work. Use `request_clarification` for human input."
    )
    parts.append(
        "Use `complete_task` when done. Do NOT route to agents not listed above."
    )

    return "\n".join(parts)
```

- [ ] **Step 3: Run tests -- all pass**

---

## Task 3: CLI Provider Resolution

**Files:**
- Modify: `src/taskbrew/agents/provider.py`
- Test: `tests/test_routing_engine.py`

The existing `ProviderRegistry` and `detect_provider()` already handle model-to-CLI mapping. This task adds a convenience function `resolve_cli_provider()` that combines model detection with a fallback to the team-level `cli_provider` setting, and tests to verify all model patterns from the spec.

- [ ] **Step 1: Write failing tests for CLI provider resolution**

```python
# tests/test_routing_engine.py (append to existing file)

from taskbrew.agents.provider import resolve_cli_provider, ProviderRegistry


class TestCLIProviderResolution:
    """Test model-to-CLI-tool mapping (spec section 5.1 step 4)."""

    def test_claude_models(self):
        assert resolve_cli_provider("claude-opus-4-6") == "claude"
        assert resolve_cli_provider("claude-sonnet-4-6") == "claude"
        assert resolve_cli_provider("claude-haiku-4-5") == "claude"

    def test_gemini_models(self):
        assert resolve_cli_provider("gemini-pro") == "gemini"
        assert resolve_cli_provider("gemini-flash") == "gemini"
        assert resolve_cli_provider("gemini-3.1-pro-preview") == "gemini"

    def test_unknown_model_uses_fallback(self):
        assert resolve_cli_provider("gpt-4o", fallback="claude") == "claude"
        assert resolve_cli_provider("unknown-model") == "claude"

    def test_none_model_uses_fallback(self):
        assert resolve_cli_provider(None, fallback="gemini") == "gemini"
        assert resolve_cli_provider(None) == "claude"

    def test_provider_registry_detect(self):
        registry = ProviderRegistry()
        registry.register_builtins()
        assert registry.detect("claude-opus-4-6") == "claude"
        assert registry.detect("gemini-pro") == "gemini"
        assert registry.detect("unknown") == "claude"
```

- [ ] **Step 2: Implement resolve_cli_provider()**

Add to `src/taskbrew/agents/provider.py`:

```python
def resolve_cli_provider(
    model: str | None,
    fallback: str = "claude",
) -> str:
    """Resolve the CLI tool name from a model string.

    Uses prefix matching: ``claude-*`` -> ``"claude"``,
    ``gemini-*`` -> ``"gemini"``.  Falls back to *fallback* if the model
    is None or unrecognised.

    This is a thin wrapper around ``detect_provider`` with a clearer name
    for use in the orchestrator startup path.
    """
    return detect_provider(model=model, cli_provider=fallback)
```

- [ ] **Step 3: Run tests -- all pass**

---

## Task 4: Integration Test -- Preset to System Prompt

**Files:**
- Test: `tests/test_routing_engine.py`

This test verifies the full pipeline: load a preset YAML, create a role config, construct pipeline edges, build the system prompt, and resolve the CLI provider. No database or async needed -- purely in-memory.

- [ ] **Step 1: Write integration test**

```python
# tests/test_routing_engine.py (append to existing file)

from taskbrew.config_loader import load_presets, PipelineEdge, PipelineConfig
from taskbrew.orchestrator.system_prompt_builder import build_task_system_prompt
from taskbrew.agents.provider import resolve_cli_provider


class TestPresetToPipelineIntegration:
    """End-to-end: preset -> role config -> pipeline -> system prompt."""

    def test_preset_to_system_prompt(self):
        """Load a preset, wire it into a pipeline, build a system prompt."""
        # 1. Load presets from disk
        presets_dir = Path(__file__).parent.parent / "config" / "presets"
        if not presets_dir.exists():
            pytest.skip("Preset files not found -- run Plan 1 first")
        presets = load_presets(presets_dir)
        assert "coder_be" in presets, "coder_be preset must exist"

        coder_preset = presets["coder_be"]

        # 2. Verify preset has expected fields
        assert coder_preset.role == "coder_be"
        assert coder_preset.approval_mode == "auto"
        assert coder_preset.uses_worktree is True
        assert "implementation" in coder_preset.produces

        # 3. Build a pipeline with this preset
        pipeline = PipelineConfig(
            start_agent="pm",
            edges=[
                PipelineEdge(
                    id="e1",
                    from_agent="pm",
                    to_agent="coder_be",
                    task_types=["implementation"],
                ),
                PipelineEdge(
                    id="e2",
                    from_agent="coder_be",
                    to_agent="architect_reviewer",
                    task_types=["verification"],
                ),
                PipelineEdge(
                    id="e3",
                    from_agent="architect_reviewer",
                    to_agent="coder_be",
                    task_types=["revision"],
                ),
            ],
        )

        # 4. Build system prompt for a task assigned to coder_be
        task = {
            "id": "CB-001",
            "title": "Implement /api/users endpoint",
            "group_id": "FEAT-001",
            "priority": "high",
            "task_type": "implementation",
            "description": "Build CRUD for users with JWT auth.",
            "chain_id": None,
        }
        prompt = build_task_system_prompt(
            agent_role="coder_be",
            task=task,
            pipeline=pipeline,
            context={
                "parent_artifact": "## Tech Design\nUse PostgreSQL + FastAPI.",
            },
        )

        # 5. Verify prompt structure
        assert "Agent Role: coder_be" in prompt
        assert "architect_reviewer" in prompt
        assert "verification" in prompt
        assert "Tech Design" in prompt
        assert "None -- first attempt" in prompt  # no rejection history

        # Coder should NOT see pm in its connected agents (incoming only)
        lines = prompt.split("\n")
        connected_section = False
        connected_agents = []
        for line in lines:
            if "== CONNECTED AGENTS ==" in line:
                connected_section = True
                continue
            if connected_section and line.startswith("- "):
                connected_agents.append(line)
            if connected_section and line.startswith("=="):
                break
        assert len(connected_agents) == 1
        assert "architect_reviewer" in connected_agents[0]

        # 6. Verify CLI provider resolution
        provider = resolve_cli_provider(coder_preset.model)
        assert provider == "claude"  # coder_be default_model is claude-sonnet-4-6

    def test_gemini_preset_provider_resolution(self):
        """A preset with a gemini model resolves to the gemini CLI."""
        provider = resolve_cli_provider("gemini-pro")
        assert provider == "gemini"

    def test_execution_config_integration(self, tmp_path):
        """ExecutionConfig integrates with TeamConfig loading."""
        team_yaml = tmp_path / "team.yaml"
        team_yaml.write_text(textwrap.dedent("""\
            team_name: "Integration Test Team"
            database:
              path: ":memory:"
            dashboard:
              host: "0.0.0.0"
              port: 8420
            artifacts:
              base_dir: "artifacts"
            defaults:
              max_instances: 1
              poll_interval_seconds: 5
              idle_timeout_minutes: 30
            execution:
              max_concurrent_api_calls: 8
              base_branch: "develop"
              max_pipeline_depth: 30
        """))
        cfg = load_team_config(team_yaml)

        # ExecutionConfig parsed correctly
        assert cfg.execution.max_concurrent_api_calls == 8
        assert cfg.execution.base_branch == "develop"
        assert cfg.execution.max_pipeline_depth == 30
        assert cfg.execution.worktree_retention_days == 7  # default

        # CLI provider for a claude model
        assert resolve_cli_provider("claude-opus-4-6") == "claude"
```

- [ ] **Step 2: Run integration test -- passes**

---

## Summary of Changes

| File | Change |
|------|--------|
| `src/taskbrew/config_loader.py` | Add `ExecutionConfig` dataclass, add `execution` field to `TeamConfig`, update `load_team_config()` |
| `src/taskbrew/orchestrator/system_prompt_builder.py` | New file -- `build_task_system_prompt()` per spec 4.1 |
| `src/taskbrew/agents/provider.py` | Add `resolve_cli_provider()` convenience function |
| `tests/test_routing_engine.py` | New file -- all tests for Plan 4 |

## What This Plan Does NOT Cover (deferred to runtime integration)

These require the full orchestrator running and are tracked separately:

- **`route_task` enforcement** -- Updating the MCP endpoint to create real tasks, validate pipeline edges, enforce revision limits. Requires Plan 3's MCP router.
- **Deferred activation** -- Tasks starting as `blocked_by` parent. Already supported by `TaskBoard.create_task()`.
- **`complete_task` integration** -- Activating downstream tasks on completion. Already supported by `TaskBoard._resolve_dependencies()`.
- **Worktree lifecycle** -- Branch-from-base, merge-on-completion, merge queue. Already supported by `WorktreeManager`.
- **API concurrency semaphore** -- Requires `asyncio.Semaphore` in the orchestrator main loop.

These are runtime behaviors that compose the pieces built in Plans 1-4. They will be wired together when the orchestrator startup path (`main.py`) is updated to use `ExecutionConfig`, `build_task_system_prompt`, and `resolve_cli_provider`.
