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
