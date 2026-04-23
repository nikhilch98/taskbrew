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
        # uses_worktree default is now None (= "auto-detect from tools")
        # rather than False. main._resolve_needs_worktree decides at
        # spawn time based on the tool list.
        assert rc.uses_worktree is None
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


from httpx import AsyncClient, ASGITransport


@pytest.fixture
def preset_app():
    """Create a minimal FastAPI app with just the presets router for testing."""
    from fastapi import FastAPI
    from taskbrew.dashboard.routers.presets import router
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
async def preset_client(preset_app):
    transport = ASGITransport(app=preset_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestPresetsAPI:
    """Test /api/presets endpoints."""

    @pytest.mark.asyncio
    async def test_list_presets(self, preset_client):
        resp = await preset_client.get("/api/presets")
        assert resp.status_code == 200
        data = resp.json()
        assert "presets" in data
        assert data["count"] >= 22
        first = data["presets"][0]
        assert "preset_id" in first
        assert "category" in first
        assert "display_name" in first
        assert "description" in first
        assert "system_prompt" not in first

    @pytest.mark.asyncio
    async def test_get_preset_detail(self, preset_client):
        resp = await preset_client.get("/api/presets/pm")
        assert resp.status_code == 200
        data = resp.json()
        assert data["preset_id"] == "pm"
        assert "system_prompt" in data
        assert "tools" in data
        assert "capabilities" in data

    @pytest.mark.asyncio
    async def test_get_preset_not_found(self, preset_client):
        resp = await preset_client.get("/api/presets/nonexistent")
        assert resp.status_code == 404


class TestPresetIntegration:
    """End-to-end test: load presets, verify completeness, parse as roles."""

    def test_all_presets_have_required_fields(self):
        from taskbrew.config_loader import load_presets
        presets = load_presets(Path("config/presets"))
        assert len(presets) == 22, f"Expected 22 presets, got {len(presets)}"
        required = {"preset_id", "category", "display_name", "description", "system_prompt", "tools", "default_model"}
        for pid, p in presets.items():
            missing = required - set(p.keys())
            assert not missing, f"Preset {pid} missing fields: {missing}"

    def test_all_presets_parseable_as_roles(self):
        from taskbrew.config_loader import load_presets, _parse_role
        presets = load_presets(Path("config/presets"))
        for pid, p in presets.items():
            data = dict(p)
            data["role"] = pid
            data["model"] = data.pop("default_model", "claude-sonnet-4-6")
            # Remap icon_emoji -> emoji so _parse_role's required key check passes
            if "icon_emoji" in data and "emoji" not in data:
                data["emoji"] = data.pop("icon_emoji")
            for key in ("preset_id", "category", "description", "capabilities", "icon_emoji"):
                data.pop(key, None)
            rc = _parse_role(data)
            assert rc.role == pid
            assert rc.approval_mode in ("auto", "manual", "first_run")

    def test_preset_categories_match_spec(self):
        from taskbrew.config_loader import load_presets
        presets = load_presets(Path("config/presets"))
        expected_categories = {"planning", "architecture", "review", "coding", "design", "testing", "security", "ops", "docs", "research", "api"}
        actual_categories = {p["category"] for p in presets.values()}
        assert actual_categories == expected_categories, f"Category mismatch: {actual_categories} vs {expected_categories}"

    def test_no_duplicate_prefixes_across_presets(self):
        from taskbrew.config_loader import load_presets
        presets = load_presets(Path("config/presets"))
        prefixes = {}
        for pid, p in presets.items():
            prefix = p.get("prefix", "")
            assert prefix not in prefixes, f"Duplicate prefix '{prefix}' in presets: {prefixes[prefix]} and {pid}"
            prefixes[prefix] = pid

    def test_uses_worktree_matches_expected(self):
        from taskbrew.config_loader import load_presets
        presets = load_presets(Path("config/presets"))
        # Non-coding agents should NOT use worktrees
        no_worktree = {"pm", "architect", "database_architect", "technical_writer", "research_agent", "api_designer"}
        for pid, p in presets.items():
            if pid in no_worktree:
                assert p.get("uses_worktree") is False, f"{pid} should have uses_worktree=false"
            elif pid.startswith("coder_") or pid.startswith("designer_") or pid.startswith("qa_tester_") or pid in ("architect_reviewer", "security_auditor", "devops_engineer"):
                assert p.get("uses_worktree") is True, f"{pid} should have uses_worktree=true"
