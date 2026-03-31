"""Tests for agent preset system and extended RoleConfig fields."""

import pytest
from taskbrew.config_loader import RoleConfig, _parse_role


class TestRoleConfigNewFields:
    """Test new fields on RoleConfig: approval_mode, max_revision_cycles, etc."""

    def test_parse_role_with_new_fields(self):
        data = {
            "role": "test_agent",
            "display_name": "Test Agent",
            "prefix": "TA",
            "color": "#ff0000",
            "emoji": "\U0001F916",
            "system_prompt": "You are a test agent.",
            "approval_mode": "manual",
            "max_revision_cycles": 5,
            "max_clarification_requests": 10,
            "max_route_tasks": 100,
            "uses_worktree": True,
            "capabilities": ["Cap 1", "Cap 2"],
            "artifact_exclude_patterns": ["*.env"],
        }
        rc = _parse_role(data)
        assert rc.approval_mode == "manual"
        assert rc.max_revision_cycles == 5
        assert rc.max_clarification_requests == 10
        assert rc.max_route_tasks == 100
        assert rc.uses_worktree is True
        assert rc.capabilities == ["Cap 1", "Cap 2"]
        assert rc.artifact_exclude_patterns == ["*.env"]

    def test_parse_role_defaults_for_new_fields(self):
        data = {
            "role": "minimal",
            "display_name": "Minimal",
            "prefix": "MN",
            "color": "#000000",
            "emoji": "\U0001F916",
            "system_prompt": "Minimal agent.",
        }
        rc = _parse_role(data)
        assert rc.approval_mode == "auto"
        assert rc.max_revision_cycles == 0
        assert rc.max_clarification_requests == 10
        assert rc.max_route_tasks == 100
        assert rc.uses_worktree is False
        assert rc.capabilities == []
        assert rc.artifact_exclude_patterns == []

    def test_invalid_approval_mode_rejected(self):
        data = {
            "role": "bad",
            "display_name": "Bad",
            "prefix": "BD",
            "color": "#000000",
            "emoji": "\U0001F916",
            "system_prompt": "Bad agent.",
            "approval_mode": "invalid_mode",
        }
        with pytest.raises(ValueError, match="approval_mode"):
            _parse_role(data)


from pathlib import Path
from taskbrew.config_loader import load_presets


class TestLoadPresets:
    """Test preset YAML loading."""

    def test_load_presets_from_directory(self, tmp_path):
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        (preset_dir / "test_agent.yaml").write_text(
            "preset_id: test_agent\n"
            "category: testing\n"
            "display_name: Test Agent\n"
            "description: A test agent\n"
            "capabilities:\n"
            "  - Does testing\n"
            "icon_emoji: '\\U0001F916'\n"
            "color: '#ff0000'\n"
            "prefix: TA\n"
            "approval_mode: auto\n"
            "max_revision_cycles: 5\n"
            "uses_worktree: true\n"
            "system_prompt: You are a test agent.\n"
            "tools: [Read, Write]\n"
            "default_model: claude-sonnet-4-6\n"
            "produces: [implementation]\n"
            "accepts: [implementation]\n"
            "max_instances: 1\n"
            "max_turns: 50\n"
            "max_execution_time: 1800\n"
            "context_includes: [parent_artifact]\n"
        )
        presets = load_presets(preset_dir)
        assert len(presets) == 1
        assert "test_agent" in presets
        p = presets["test_agent"]
        assert p["preset_id"] == "test_agent"
        assert p["category"] == "testing"
        assert p["display_name"] == "Test Agent"
        assert p["capabilities"] == ["Does testing"]
        assert p["default_model"] == "claude-sonnet-4-6"

    def test_load_presets_empty_dir(self, tmp_path):
        preset_dir = tmp_path / "presets"
        preset_dir.mkdir()
        presets = load_presets(preset_dir)
        assert presets == {}

    def test_load_presets_missing_dir(self, tmp_path):
        presets = load_presets(tmp_path / "nonexistent")
        assert presets == {}
