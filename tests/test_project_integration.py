"""Integration tests for multi-project support.

These tests verify end-to-end scenarios: auto-migration of existing configs,
project creation with/without defaults, project switching with isolated config,
delete clearing active state, and orchestrator lifecycle (activate/deactivate).

Unit tests live in test_project_manager.py — this file focuses on integration
scenarios that exercise multiple operations in sequence.
"""

from __future__ import annotations

import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from taskbrew.project_manager import ProjectManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm_with_registry(tmp_path):
    """Return a ProjectManager wired to a fresh temporary registry."""
    registry = tmp_path / "registry" / "projects.yaml"
    return ProjectManager(registry_path=registry)


# ---------------------------------------------------------------------------
# Auto-migration: existing config/team.yaml is preserved on registration
# ---------------------------------------------------------------------------


class TestAutoMigration:
    """When a directory already has config/team.yaml, registering it
    via create_project must not overwrite the existing config."""

    def test_existing_config_not_overwritten(self, pm_with_registry, tmp_path):
        """Simulate existing project dir with config/team.yaml."""
        project_dir = tmp_path / "existing-project"
        project_dir.mkdir()
        config_dir = project_dir / "config"
        config_dir.mkdir()
        (config_dir / "team.yaml").write_text("team_name: Legacy\n")
        (config_dir / "roles").mkdir()

        # Register as if auto-migration happened
        pm_with_registry.create_project("Existing Project", str(project_dir))
        projects = pm_with_registry.list_projects()
        assert len(projects) == 1
        assert projects[0]["directory"] == str(project_dir)

        # Config should NOT be overwritten
        content = (config_dir / "team.yaml").read_text()
        assert "Legacy" in content

    def test_existing_role_files_not_overwritten(self, pm_with_registry, tmp_path):
        """Pre-existing role YAML files should be kept intact."""
        project_dir = tmp_path / "has-roles"
        project_dir.mkdir()
        config_dir = project_dir / "config"
        roles_dir = config_dir / "roles"
        roles_dir.mkdir(parents=True)
        (config_dir / "team.yaml").write_text("team_name: Old\n")
        (roles_dir / "pm.yaml").write_text("role: pm\ncustom_field: true\n")

        pm_with_registry.create_project("Has Roles", str(project_dir), with_defaults=True)

        data = yaml.safe_load((roles_dir / "pm.yaml").read_text())
        # The original custom_field should still be present
        assert data.get("custom_field") is True

    def test_auto_register_creates_correct_slug(self, pm_with_registry, tmp_path):
        d = tmp_path / "my-cool-app"
        d.mkdir()
        result = pm_with_registry.create_project("My Cool App", str(d))
        assert result["id"] == "my-cool-app"

    def test_auto_register_with_special_chars(self, pm_with_registry, tmp_path):
        d = tmp_path / "app"
        d.mkdir()
        result = pm_with_registry.create_project("My App! (v2.0)", str(d))
        assert result["id"] == "my-app-v20"


# ---------------------------------------------------------------------------
# Project switching: creating two projects, each has isolated config
# ---------------------------------------------------------------------------


class TestProjectSwitching:
    """Two projects registered in the same registry must have fully isolated
    configuration directories and the active pointer must track correctly."""

    def test_create_two_projects(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "project-a"
        d2 = tmp_path / "project-b"
        pm_with_registry.create_project("Project A", str(d1))
        pm_with_registry.create_project("Project B", str(d2))
        assert len(pm_with_registry.list_projects()) == 2

    def test_each_project_has_isolated_config(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "proj1"
        d2 = tmp_path / "proj2"
        pm_with_registry.create_project("Proj1", str(d1))
        pm_with_registry.create_project("Proj2", str(d2))

        # Each has its own team.yaml with its own team_name
        t1 = yaml.safe_load((d1 / "config" / "team.yaml").read_text())
        t2 = yaml.safe_load((d2 / "config" / "team.yaml").read_text())
        assert t1["team_name"] == "Proj1"
        assert t2["team_name"] == "Proj2"

    def test_each_project_has_its_own_roles(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "p1"
        d2 = tmp_path / "p2"
        pm_with_registry.create_project("P1", str(d1), with_defaults=True)
        pm_with_registry.create_project("P2", str(d2), with_defaults=False)

        assert (d1 / "config" / "roles" / "pm.yaml").exists()
        assert not (d2 / "config" / "roles" / "pm.yaml").exists()

    def test_switch_active_between_projects(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        pm_with_registry.create_project("A", str(d1))
        pm_with_registry.create_project("B", str(d2))

        pm_with_registry.set_active("a")
        assert pm_with_registry.get_active()["id"] == "a"

        pm_with_registry.set_active("b")
        assert pm_with_registry.get_active()["id"] == "b"

    def test_delete_clears_active_if_same(self, pm_with_registry, tmp_path):
        d = tmp_path / "to-delete"
        pm_with_registry.create_project("To Delete", str(d))
        pm_with_registry.set_active("to-delete")
        pm_with_registry.delete_project("to-delete")
        assert pm_with_registry.get_active() is None

    def test_delete_preserves_active_if_different(self, pm_with_registry, tmp_path):
        d1 = tmp_path / "keep"
        d2 = tmp_path / "delete-me"
        pm_with_registry.create_project("Keep", str(d1))
        pm_with_registry.create_project("Delete Me", str(d2))
        pm_with_registry.set_active("keep")
        pm_with_registry.delete_project("delete-me")
        active = pm_with_registry.get_active()
        assert active is not None
        assert active["id"] == "keep"


# ---------------------------------------------------------------------------
# ProjectManager lifecycle: activate / deactivate with mocked orchestrator
# ---------------------------------------------------------------------------


class TestLifecycleIntegration:
    """Async lifecycle tests with a mocked orchestrator to verify the full
    activate -> deactivate cycle, including edge cases."""

    async def test_activate_deactivate_cycle(self, pm_with_registry, tmp_path):
        """Full cycle: create -> activate -> deactivate."""
        d = tmp_path / "lifecycle"
        pm_with_registry.create_project("Lifecycle", str(d))

        mock_orch = MagicMock()
        mock_orch.shutdown = AsyncMock()

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch,
        ):
            result = await pm_with_registry.activate_project("lifecycle")
            assert result is mock_orch
            assert pm_with_registry.orchestrator is mock_orch
            assert pm_with_registry.get_active()["id"] == "lifecycle"

        # Deactivate
        await pm_with_registry.deactivate_current()
        mock_orch.shutdown.assert_awaited_once()
        assert pm_with_registry.orchestrator is None
        assert pm_with_registry.get_active() is None

    async def test_activate_missing_dir_removes_from_registry(
        self, pm_with_registry, tmp_path
    ):
        d = tmp_path / "will-vanish"
        pm_with_registry.create_project("Will Vanish", str(d))

        # Delete the directory after creation
        shutil.rmtree(d)

        with pytest.raises(FileNotFoundError, match="no longer exists"):
            await pm_with_registry.activate_project("will-vanish")

        # Should be auto-removed from registry
        assert pm_with_registry.list_projects() == []

    async def test_activate_missing_team_yaml_raises(
        self, pm_with_registry, tmp_path
    ):
        d = tmp_path / "no-config"
        pm_with_registry.create_project("No Config", str(d), with_defaults=False)

        # Remove the team.yaml that scaffolding created
        (d / "config" / "team.yaml").unlink()

        with pytest.raises(FileNotFoundError, match="team.yaml"):
            await pm_with_registry.activate_project("no-config")

    async def test_switching_projects_deactivates_previous(
        self, pm_with_registry, tmp_path
    ):
        """Activating project B while A is active should shut down A's orchestrator."""
        d1 = tmp_path / "first"
        d2 = tmp_path / "second"
        pm_with_registry.create_project("First", str(d1))
        pm_with_registry.create_project("Second", str(d2))

        mock_orch1 = MagicMock()
        mock_orch1.shutdown = AsyncMock()
        mock_orch2 = MagicMock()

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch1,
        ):
            await pm_with_registry.activate_project("first")

        with patch(
            "taskbrew.main.build_orchestrator",
            new_callable=AsyncMock,
            return_value=mock_orch2,
        ):
            await pm_with_registry.activate_project("second")

        mock_orch1.shutdown.assert_awaited_once()
        assert pm_with_registry.orchestrator is mock_orch2
        assert pm_with_registry.get_active()["id"] == "second"


# ---------------------------------------------------------------------------
# Registry persistence across instances
# ---------------------------------------------------------------------------


class TestRegistryPersistence:
    """Verify that multiple ProjectManager instances sharing the same
    registry file see a consistent view of projects and active state."""

    def test_new_instance_reads_same_registry(self, tmp_path):
        registry = tmp_path / "reg" / "projects.yaml"

        pm1 = ProjectManager(registry_path=registry)
        d = tmp_path / "proj"
        pm1.create_project("Proj", str(d))
        pm1.set_active("proj")

        # New instance reads the same registry
        pm2 = ProjectManager(registry_path=registry)
        assert len(pm2.list_projects()) == 1
        assert pm2.get_active()["id"] == "proj"

    def test_multiple_creates_across_instances(self, tmp_path):
        registry = tmp_path / "reg" / "projects.yaml"

        pm1 = ProjectManager(registry_path=registry)
        d1 = tmp_path / "a"
        pm1.create_project("A", str(d1))

        pm2 = ProjectManager(registry_path=registry)
        d2 = tmp_path / "b"
        pm2.create_project("B", str(d2))

        pm3 = ProjectManager(registry_path=registry)
        assert len(pm3.list_projects()) == 2

    def test_delete_from_one_instance_visible_to_another(self, tmp_path):
        registry = tmp_path / "reg" / "projects.yaml"

        pm1 = ProjectManager(registry_path=registry)
        d = tmp_path / "ephemeral"
        pm1.create_project("Ephemeral", str(d))
        assert len(pm1.list_projects()) == 1

        pm2 = ProjectManager(registry_path=registry)
        pm2.delete_project("ephemeral")

        # pm1 re-reads from disk, so it sees the deletion
        assert len(pm1.list_projects()) == 0
