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


# ---------------------------------------------------------------------------
# Task 2: System Prompt Builder
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Task 3: CLI Provider Resolution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Task 4: Integration Test — Preset to System Prompt
# ---------------------------------------------------------------------------

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

        # 2. Verify preset has expected fields (presets are raw dicts)
        assert coder_preset["preset_id"] == "coder_be"
        assert coder_preset["approval_mode"] == "auto"
        assert coder_preset["uses_worktree"] is True
        assert "implementation" in coder_preset["produces"]

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
        provider = resolve_cli_provider(coder_preset["default_model"])
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
