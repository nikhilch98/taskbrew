"""Tests for taskbrew.project_manager — registry CRUD, scaffolding, and lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from taskbrew.project_manager import ProjectManager, _slugify


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    """Return a temporary path for the projects registry YAML."""
    return tmp_path / "registry" / "projects.yaml"


@pytest.fixture
def pm(registry_path: Path) -> ProjectManager:
    """Return a ProjectManager wired to a temporary registry."""
    return ProjectManager(registry_path=registry_path)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Return an absolute temporary path to use as a project directory."""
    d = tmp_path / "my-project"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Slug tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic_spaces(self):
        assert _slugify("My SaaS App") == "my-saas-app"

    def test_special_characters_removed(self):
        assert _slugify("Hello!! World@#$%") == "hello-world"

    def test_leading_trailing_whitespace(self):
        assert _slugify("  spaced out  ") == "spaced-out"

    def test_multiple_hyphens_collapsed(self):
        assert _slugify("a---b") == "a-b"

    def test_numbers_preserved(self):
        assert _slugify("Project 123") == "project-123"

    def test_empty_becomes_empty(self):
        assert _slugify("!!!") == ""


# ---------------------------------------------------------------------------
# Registry CRUD tests
# ---------------------------------------------------------------------------


class TestRegistryCRUD:
    def test_list_empty_when_no_registry_file(self, pm: ProjectManager):
        """list_projects returns [] when registry file doesn't exist."""
        assert pm.list_projects() == []

    def test_create_project_returns_correct_entry(
        self, pm: ProjectManager, project_dir: Path
    ):
        entry = pm.create_project("My App", str(project_dir))
        assert entry["id"] == "my-app"
        assert entry["name"] == "My App"
        assert entry["directory"] == str(project_dir)
        assert entry["auto_resume_on_activate"] is True
        assert "created_at" in entry

    def test_create_persists_to_yaml(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("My App", str(project_dir))
        assert pm.registry_path.exists()

        with open(pm.registry_path) as f:
            data = yaml.safe_load(f)
        assert len(data["projects"]) == 1
        assert data["projects"][0]["id"] == "my-app"

    def test_create_makes_missing_directory(
        self, pm: ProjectManager, tmp_path: Path
    ):
        new_dir = tmp_path / "brand-new"
        assert not new_dir.exists()
        pm.create_project("Brand New", str(new_dir))
        assert new_dir.is_dir()

    def test_create_rejects_relative_path(self, pm: ProjectManager):
        with pytest.raises(ValueError, match="absolute path"):
            pm.create_project("Relative", "relative/path")

    def test_create_rejects_duplicate_id(
        self, pm: ProjectManager, tmp_path: Path
    ):
        dir1 = tmp_path / "d1"
        dir2 = tmp_path / "d2"
        dir1.mkdir()
        dir2.mkdir()

        pm.create_project("Dup Test", str(dir1))
        with pytest.raises(ValueError, match="already exists"):
            pm.create_project("Dup Test", str(dir2))

    def test_list_returns_all_projects(
        self, pm: ProjectManager, tmp_path: Path
    ):
        for i in range(3):
            d = tmp_path / f"proj-{i}"
            d.mkdir()
            pm.create_project(f"Proj {i}", str(d))
        assert len(pm.list_projects()) == 3

    def test_delete_removes_from_registry(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("To Delete", str(project_dir))
        assert len(pm.list_projects()) == 1
        pm.delete_project("to-delete")
        assert len(pm.list_projects()) == 0

    def test_delete_does_not_remove_files(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Keep Files", str(project_dir))
        pm.delete_project("keep-files")
        assert project_dir.is_dir()

    def test_delete_nonexistent_raises_key_error(self, pm: ProjectManager):
        with pytest.raises(KeyError, match="nope"):
            pm.delete_project("nope")

    def test_get_active_returns_none_initially(self, pm: ProjectManager):
        assert pm.get_active() is None

    def test_set_active_persists_to_yaml(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Active", str(project_dir))
        pm.set_active("active")

        with open(pm.registry_path) as f:
            data = yaml.safe_load(f)
        assert data["active_project"] == "active"

    def test_get_active_returns_project_after_set(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Active", str(project_dir))
        pm.set_active("active")
        active = pm.get_active()
        assert active is not None
        assert active["id"] == "active"

    def test_clear_active_works(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Active", str(project_dir))
        pm.set_active("active")
        pm.clear_active()
        assert pm.get_active() is None

    def test_set_active_nonexistent_raises_key_error(
        self, pm: ProjectManager
    ):
        with pytest.raises(KeyError, match="no-such"):
            pm.set_active("no-such")

    def test_corrupted_registry_resets_gracefully(
        self, pm: ProjectManager, project_dir: Path
    ):
        """Writing garbage to the registry file should not crash list_projects."""
        pm.registry_path.parent.mkdir(parents=True, exist_ok=True)
        pm.registry_path.write_text("not: [valid: yaml: {{{{")

        # Should fall back to empty
        projects = pm.list_projects()
        assert projects == []

    def test_corrupted_registry_not_dict_resets(
        self, pm: ProjectManager
    ):
        """Registry file containing a scalar should reset to default."""
        pm.registry_path.parent.mkdir(parents=True, exist_ok=True)
        pm.registry_path.write_text("just a string\n")

        projects = pm.list_projects()
        assert projects == []

    def test_delete_active_project_clears_active(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Active", str(project_dir))
        pm.set_active("active")
        pm.delete_project("active")
        assert pm.get_active() is None

    def test_auto_resume_flag_is_consumed_once(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("First Run", str(project_dir))

        assert pm.should_auto_resume_on_activate("first-run") is True
        pm.mark_auto_resume_consumed("first-run")

        projects = pm.list_projects()
        assert projects[0]["auto_resume_on_activate"] is False
        assert pm.should_auto_resume_on_activate("first-run") is False


# ---------------------------------------------------------------------------
# Scaffolding tests
# ---------------------------------------------------------------------------


class TestScaffolding:
    def test_creates_config_and_roles_dirs(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "scaffolded"
        pm.create_project("Scaffolded", str(d))
        assert (d / "config").is_dir()
        assert (d / "config" / "roles").is_dir()

    def test_creates_team_yaml_with_project_name(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "named"
        pm.create_project("Named Project", str(d))
        team_yaml = d / "config" / "team.yaml"
        assert team_yaml.exists()

        with open(team_yaml) as f:
            data = yaml.safe_load(f)
        assert data["team_name"] == "Named Project"

    def test_with_defaults_creates_core_role_files_without_verifier(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "defaults"
        pm.create_project("Defaults", str(d), with_defaults=True)
        roles_dir = d / "config" / "roles"
        role_files = sorted(p.stem for p in roles_dir.glob("*.yaml"))
        assert role_files == ["architect", "coder", "pm"]

    def test_default_roles_allow_completion_tool(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "defaults-complete-tool"
        pm.create_project("Defaults Complete Tool", str(d), with_defaults=True)

        for role_name in ("pm", "architect", "coder"):
            with open(d / "config" / "roles" / f"{role_name}.yaml") as f:
                role_data = yaml.safe_load(f)
            assert "mcp__task-tools__complete_task" in role_data["tools"]
        with open(d / "config" / "roles" / "pm.yaml") as f:
            pm_data = yaml.safe_load(f)
        with open(d / "config" / "roles" / "architect.yaml") as f:
            architect_data = yaml.safe_load(f)
        assert "mcp__task-tools__create_work_package" in pm_data["tools"]
        assert "mcp__task-tools__update_work_package" in architect_data["tools"]

    def test_without_defaults_leaves_roles_empty(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "no-defaults"
        pm.create_project("No Defaults", str(d), with_defaults=False)
        roles_dir = d / "config" / "roles"
        assert roles_dir.is_dir()
        assert list(roles_dir.glob("*.yaml")) == []

    def test_existing_team_yaml_not_overwritten(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "existing"
        config_dir = d / "config"
        config_dir.mkdir(parents=True)
        team_yaml = config_dir / "team.yaml"
        team_yaml.write_text('team_name: "ORIGINAL"\n')

        pm.create_project("Existing", str(d))

        with open(team_yaml) as f:
            data = yaml.safe_load(f)
        assert data["team_name"] == "ORIGINAL"

    def test_default_roles_have_required_fields(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "check-fields"
        pm.create_project("Check Fields", str(d))
        roles_dir = d / "config" / "roles"

        required_fields = [
            "role", "display_name", "prefix", "color", "emoji",
            "system_prompt", "tools", "model", "produces", "accepts",
            "routes_to", "max_instances", "context_includes",
        ]
        for role_file in roles_dir.glob("*.yaml"):
            with open(role_file) as f:
                data = yaml.safe_load(f)
            for field in required_fields:
                assert field in data, f"Missing '{field}' in {role_file.name}"

    def test_default_roles_use_selected_provider_models(
        self, pm: ProjectManager, tmp_path: Path
    ):
        claude_dir = tmp_path / "claude-defaults"
        pm.create_project("Claude Defaults", str(claude_dir), cli_provider="claude")
        with open(claude_dir / "config" / "roles" / "pm.yaml") as f:
            claude_pm = yaml.safe_load(f)
        with open(claude_dir / "config" / "roles" / "coder.yaml") as f:
            claude_coder = yaml.safe_load(f)
        assert claude_pm["model"] == "claude-opus-4-7"
        assert claude_pm["reasoning_effort"] == "xhigh"
        assert claude_coder["model"] == "claude-sonnet-4-6"
        assert claude_coder["reasoning_effort"] == "high"

        gemini_dir = tmp_path / "gemini-defaults"
        pm.create_project("Gemini Defaults", str(gemini_dir), cli_provider="gemini")
        with open(gemini_dir / "config" / "roles" / "pm.yaml") as f:
            gemini_pm = yaml.safe_load(f)
        with open(gemini_dir / "config" / "roles" / "coder.yaml") as f:
            gemini_coder = yaml.safe_load(f)
        assert gemini_pm["model"] == "gemini-3-pro-preview"
        assert gemini_pm["reasoning_effort"] == "high"
        assert gemini_coder["model"] == "gemini-3-flash-preview"
        assert gemini_coder["reasoning_effort"] == "medium"

        codex_dir = tmp_path / "codex-defaults"
        pm.create_project("Codex Defaults", str(codex_dir), cli_provider="codex")
        with open(codex_dir / "config" / "roles" / "coder.yaml") as f:
            codex_coder = yaml.safe_load(f)
        assert codex_coder["model"] == "gpt-5.5"
        assert codex_coder["reasoning_effort"] == "xhigh"
        with open(codex_dir / "config" / "team.yaml") as f:
            codex_team = yaml.safe_load(f)
        assert codex_team["system_agent"] == {
            "provider": "codex",
            "model": "gpt-5.5",
            "reasoning_effort": "xhigh",
        }

    def test_create_project_defaults_to_codex_gpt55_for_all_default_agents(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "implicit-codex-defaults"
        pm.create_project("Implicit Codex Defaults", str(d))

        for role in ("pm", "architect", "coder"):
            with open(d / "config" / "roles" / f"{role}.yaml") as f:
                role_data = yaml.safe_load(f)
            assert role_data["model"] == "gpt-5.5"
            assert role_data["reasoning_effort"] == "xhigh"

        with open(d / "config" / "team.yaml") as f:
            team_data = yaml.safe_load(f)
        assert team_data["cli_provider"] == "codex"
        assert team_data["system_agent"] == {
            "provider": "codex",
            "model": "gpt-5.5",
            "reasoning_effort": "xhigh",
        }

    def test_role_model_settings_override_scaffold_defaults(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "custom-models"
        pm.create_project(
            "Custom Models",
            str(d),
            cli_provider="codex",
            role_model_settings={
                "pm": {"model": "gpt-5.3-codex", "reasoning_effort": "xhigh"},
                "coder": {"model": "gpt-5.4-mini", "reasoning_effort": "low"},
            },
        )

        with open(d / "config" / "roles" / "pm.yaml") as f:
            pm_role = yaml.safe_load(f)
        with open(d / "config" / "roles" / "coder.yaml") as f:
            coder_role = yaml.safe_load(f)

        assert pm_role["model"] == "gpt-5.3-codex"
        assert pm_role["reasoning_effort"] == "xhigh"
        assert coder_role["model"] == "gpt-5.4-mini"
        assert coder_role["reasoning_effort"] == "low"

    def test_system_agent_settings_override_scaffold_defaults(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "custom-system-agent"
        pm.create_project(
            "Custom System Agent",
            str(d),
            cli_provider="claude",
            system_agent_settings={
                "provider": "gemini",
                "model": "gemini-3-pro-preview",
                "reasoning_effort": "high",
            },
        )

        with open(d / "config" / "team.yaml") as f:
            team_data = yaml.safe_load(f)

        assert team_data["system_agent"] == {
            "provider": "gemini",
            "model": "gemini-3-pro-preview",
            "reasoning_effort": "high",
        }

    def test_pm_routes_to_architect(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "routing"
        pm.create_project("Routing", str(d))
        with open(d / "config" / "roles" / "pm.yaml") as f:
            data = yaml.safe_load(f)
        targets = [r["role"] for r in data["routes_to"]]
        assert "architect" in targets

    def test_coder_has_no_default_downstream_routes(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "routing2"
        pm.create_project("Routing2", str(d))
        with open(d / "config" / "roles" / "coder.yaml") as f:
            data = yaml.safe_load(f)
        assert data["routes_to"] == []

    def test_coder_prompt_owns_task_testing_before_system_review(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "coder-quality"
        pm.create_project("Coder Quality", str(d))
        with open(d / "config" / "roles" / "coder.yaml") as f:
            data = yaml.safe_load(f)

        prompt = data["system_prompt"]
        assert "Write or update targeted tests" in prompt
        assert "Verify the task acceptance criteria" in prompt
        assert "system review gate" in prompt
        assert "assigned_to: \"verifier\"" not in prompt


# ---------------------------------------------------------------------------
# Lifecycle tests (async, mocked)
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_activate_sets_orchestrator(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Lifecycle", str(project_dir))
        # Scaffold creates team.yaml so it should exist
        mock_orch = MagicMock()
        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch,
        ) as mock_build:
            result = await pm.activate_project("lifecycle")
            mock_build.assert_awaited_once()
            assert pm.orchestrator is mock_orch
            assert result is mock_orch

    async def test_activate_updates_registry_active(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Act", str(project_dir))
        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ):
            await pm.activate_project("act")
        assert pm.get_active()["id"] == "act"

    async def test_activate_deactivates_previous(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d1 = tmp_path / "p1"
        d2 = tmp_path / "p2"
        pm.create_project("P1", str(d1))
        pm.create_project("P2", str(d2))

        mock_orch1 = MagicMock()
        mock_orch1.shutdown = AsyncMock()
        mock_orch2 = MagicMock()

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch1,
        ):
            await pm.activate_project("p1")

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch2,
        ):
            await pm.activate_project("p2")

        mock_orch1.shutdown.assert_awaited_once()
        assert pm.orchestrator is mock_orch2

    async def test_deactivate_calls_shutdown(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Deact", str(project_dir))
        mock_orch = MagicMock()
        mock_orch.shutdown = AsyncMock()

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch,
        ):
            await pm.activate_project("deact")

        await pm.deactivate_current()
        mock_orch.shutdown.assert_awaited_once()
        assert pm.orchestrator is None

    async def test_deactivate_when_none_is_noop(self, pm: ProjectManager):
        """Deactivating with no active orchestrator should not raise."""
        await pm.deactivate_current()
        assert pm.orchestrator is None

    async def test_activate_nonexistent_raises_key_error(
        self, pm: ProjectManager
    ):
        with pytest.raises(KeyError, match="no-such"):
            await pm.activate_project("no-such")

    async def test_activate_missing_directory_raises_and_removes(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "vanished"
        d.mkdir()
        pm.create_project("Vanished", str(d))

        # Now remove the directory
        import shutil
        shutil.rmtree(d)

        with pytest.raises(FileNotFoundError, match="no longer exists"):
            await pm.activate_project("vanished")

        # Should be auto-removed from registry
        assert len(pm.list_projects()) == 0

    async def test_activate_missing_team_yaml_raises(
        self, pm: ProjectManager, tmp_path: Path
    ):
        d = tmp_path / "no-config"
        d.mkdir()
        # Register without scaffolding (bypass create_project scaffolding by
        # creating project then removing the team.yaml)
        pm.create_project("No Config", str(d), with_defaults=False)
        (d / "config" / "team.yaml").unlink()

        with pytest.raises(FileNotFoundError, match="config/team.yaml"):
            await pm.activate_project("no-config")

    async def test_deactivate_clears_active_in_registry(
        self, pm: ProjectManager, project_dir: Path
    ):
        pm.create_project("Clear", str(project_dir))
        mock_orch = MagicMock()
        mock_orch.shutdown = AsyncMock()

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch,
        ):
            await pm.activate_project("clear")

        assert pm.get_active() is not None
        await pm.deactivate_current()
        assert pm.get_active() is None

    async def test_deactivate_handles_shutdown_timeout(
        self, pm: ProjectManager, project_dir: Path
    ):
        """If shutdown takes too long, deactivate should not hang."""
        pm.create_project("Timeout", str(project_dir))

        async def slow_shutdown():
            await asyncio.sleep(60)

        mock_orch = MagicMock()
        mock_orch.shutdown = slow_shutdown

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch,
        ):
            await pm.activate_project("timeout")

        # This should complete within the 5s timeout, not hang for 60s
        await pm.deactivate_current()
        assert pm.orchestrator is None
