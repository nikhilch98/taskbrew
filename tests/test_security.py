"""Security-focused tests for hardened configuration and input validation."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.agents.instance_manager import InstanceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def app_client(tmp_path):
    """Minimal app client for API tests."""
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    from taskbrew.dashboard.app import create_app

    app = create_app(
        event_bus=event_bus,
        task_board=board,
        instance_manager=instance_mgr,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "app": app}
    await db.close()


# ------------------------------------------------------------------
# Fix 1: CORS origins from environment
# ------------------------------------------------------------------


class TestCORSOrigins:
    """CORS origins should be configurable via CORS_ORIGINS env var."""

    async def test_default_cors_origins(self, tmp_path):
        """Without CORS_ORIGINS env, defaults to localhost origins."""
        db = Database(str(tmp_path / "test.db"))
        await db.initialize()
        board = TaskBoard(db, group_prefixes={})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CORS_ORIGINS", None)
            from taskbrew.dashboard.app import create_app

            app = create_app(
                event_bus=event_bus,
                task_board=board,
                instance_manager=instance_mgr,
            )

        # Check that CORSMiddleware is configured
        cors_mw = None
        for mw in app.user_middleware:
            if mw.cls.__name__ == "CORSMiddleware":
                cors_mw = mw
                break
        assert cors_mw is not None
        origins = cors_mw.kwargs.get("allow_origins", [])
        assert "http://localhost:8000" in origins
        assert "http://localhost:3000" in origins
        assert "*" not in origins
        await db.close()

    async def test_custom_cors_origins(self, tmp_path):
        """CORS_ORIGINS env var overrides defaults."""
        db = Database(str(tmp_path / "test.db"))
        await db.initialize()
        board = TaskBoard(db, group_prefixes={})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        with patch.dict(
            os.environ,
            {"CORS_ORIGINS": "https://myapp.example.com,https://staging.example.com"},
        ):
            from taskbrew.dashboard.app import create_app

            app = create_app(
                event_bus=event_bus,
                task_board=board,
                instance_manager=instance_mgr,
            )

        cors_mw = None
        for mw in app.user_middleware:
            if mw.cls.__name__ == "CORSMiddleware":
                cors_mw = mw
                break
        assert cors_mw is not None
        origins = cors_mw.kwargs.get("allow_origins", [])
        assert "https://myapp.example.com" in origins
        assert "https://staging.example.com" in origins
        assert "http://localhost:8000" not in origins
        await db.close()


# ------------------------------------------------------------------
# Fix 2: Auth middleware blocks when enabled, allows when disabled
# ------------------------------------------------------------------


class TestAuthMiddleware:
    """Auth dependency should block/allow based on AUTH_ENABLED env var."""

    async def test_auth_disabled_allows_all(self, app_client):
        """With AUTH_ENABLED=false (default), all requests pass."""
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            resp = await app_client["client"].get("/api/health")
            assert resp.status_code == 200

    async def test_auth_disabled_restart_allowed(self, app_client):
        """With AUTH_ENABLED=false, even /api/server/restart is accessible."""
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            resp = await app_client["client"].post("/api/server/restart")
            # 200 means it got through auth (it will try to restart, that's OK)
            assert resp.status_code == 200

    async def test_auth_enabled_blocks_without_token(self, tmp_path):
        """With AUTH_ENABLED=true, requests without a valid token get 401."""
        db = Database(str(tmp_path / "test.db"))
        await db.initialize()
        board = TaskBoard(db, group_prefixes={})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        with patch.dict(os.environ, {"AUTH_ENABLED": "true"}):
            from taskbrew.dashboard.app import create_app

            app = create_app(
                event_bus=event_bus,
                task_board=board,
                instance_manager=instance_mgr,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # No Authorization header -> 401
                resp = await client.post("/api/server/restart")
                assert resp.status_code == 401

                # Invalid token -> 401
                resp = await client.post(
                    "/api/server/restart",
                    headers={"Authorization": "Bearer wrong-token"},
                )
                assert resp.status_code == 401
        await db.close()

    async def test_auth_enabled_allows_with_valid_token(self, tmp_path):
        """With AUTH_ENABLED=true and a valid token, requests pass through."""
        db = Database(str(tmp_path / "test.db"))
        await db.initialize()
        board = TaskBoard(db, group_prefixes={})
        event_bus = EventBus()
        instance_mgr = InstanceManager(db)

        # Pre-set a known token via the AUTH_TOKENS env var won't work since
        # AuthManager doesn't read that. Instead we create the app with
        # AUTH_ENABLED=true and then inject a known token into the AuthManager.
        with patch.dict(os.environ, {"AUTH_ENABLED": "true"}):
            from taskbrew.dashboard.app import create_app

            app = create_app(
                event_bus=event_bus,
                task_board=board,
                instance_manager=instance_mgr,
            )

            # Find the _auth_manager from the verify_auth closure inside the app.
            # The verify_admin dependency references verify_auth which captures
            # _auth_manager. We can get it from the route's dependencies.
            # Simpler: generate a token from the AuthManager directly.
            # Access the dependency's closure to find _auth_manager.
            from taskbrew.auth import AuthManager

            # The auth manager is captured in the verify_auth closure.
            # We can find it by looking at the restart endpoint dependency.
            auth_mgr = None
            for route in app.routes:
                if hasattr(route, "path") and route.path == "/api/server/restart":
                    for dep in route.dependencies:
                        # Navigate the dependency chain to find _auth_manager
                        # This is the verify_admin function
                        fn = dep.dependency
                        if hasattr(fn, "__code__"):
                            for cell in (fn.__code__.co_freevars and fn.__closure__) or []:
                                val = cell.cell_contents
                                if callable(val) and hasattr(val, "__name__") and val.__name__ == "verify_auth":
                                    # verify_auth captures _auth_manager
                                    for inner_cell in val.__closure__ or []:
                                        inner_val = inner_cell.cell_contents
                                        if isinstance(inner_val, AuthManager):
                                            auth_mgr = inner_val
                                            break
                    break

            assert auth_mgr is not None, "Could not find _auth_manager from app routes"
            token = auth_mgr.generate_token()

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Without token -> 401
                resp = await client.post("/api/server/restart")
                assert resp.status_code == 401

                # With valid token -> 200
                resp = await client.post(
                    "/api/server/restart",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 200
        await db.close()


# ------------------------------------------------------------------
# Fix 3: Default permission_mode is "default"
# ------------------------------------------------------------------


class TestPermissionModeDefault:
    """AgentConfig.permission_mode should default to 'default', not 'bypassPermissions'."""

    def test_default_permission_mode(self):
        from taskbrew.config import AgentConfig

        config = AgentConfig(
            name="test",
            role="tester",
            system_prompt="test prompt",
        )
        assert config.permission_mode == "default"

    def test_explicit_override_still_works(self):
        from taskbrew.config import AgentConfig

        config = AgentConfig(
            name="test",
            role="tester",
            system_prompt="test prompt",
            permission_mode="bypassPermissions",
        )
        assert config.permission_mode == "bypassPermissions"


# ------------------------------------------------------------------
# Fix 4: Git branch name validation
# ------------------------------------------------------------------


class TestGitBranchNameValidation:
    """Branch names must be validated to prevent git flag injection."""

    def test_rejects_dash_prefix(self):
        """Branch names starting with '-' are rejected (flag injection)."""
        from taskbrew.tools.git_tools import _validate_branch_name

        with pytest.raises(ValueError, match="cannot start with dash"):
            _validate_branch_name("-flag")

    def test_rejects_double_dash_exec(self):
        """Reject dangerous flag-like branch names."""
        from taskbrew.tools.git_tools import _validate_branch_name

        with pytest.raises(ValueError, match="cannot start with dash"):
            _validate_branch_name("--exec=malicious")

    def test_rejects_special_characters(self):
        """Branch names with shell metacharacters are rejected."""
        from taskbrew.tools.git_tools import _validate_branch_name

        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_branch_name("branch;rm -rf /")

    def test_rejects_spaces(self):
        """Branch names with spaces are rejected."""
        from taskbrew.tools.git_tools import _validate_branch_name

        with pytest.raises(ValueError, match="contains invalid characters"):
            _validate_branch_name("branch name")

    def test_allows_feature_branch(self):
        """Standard feature branch names should be accepted."""
        from taskbrew.tools.git_tools import _validate_branch_name

        result = _validate_branch_name("feature/foo")
        assert result == "feature/foo"

    def test_allows_hyphenated_branch(self):
        """Hyphenated branch names (not starting with dash) are valid."""
        from taskbrew.tools.git_tools import _validate_branch_name

        result = _validate_branch_name("fix/my-bug-123")
        assert result == "fix/my-bug-123"

    def test_allows_dotted_branch(self):
        """Branch names with dots are valid."""
        from taskbrew.tools.git_tools import _validate_branch_name

        result = _validate_branch_name("release/1.2.3")
        assert result == "release/1.2.3"

    def test_allows_underscored_branch(self):
        """Branch names with underscores are valid."""
        from taskbrew.tools.git_tools import _validate_branch_name

        result = _validate_branch_name("feat_new_thing")
        assert result == "feat_new_thing"


# ------------------------------------------------------------------
# Fix 5: Knowledge graph path traversal prevention
# ------------------------------------------------------------------


class TestKnowledgeGraphPathTraversal:
    """KnowledgeGraphBuilder should reject path traversal attempts."""

    @pytest.fixture
    async def db(self, tmp_path):
        db = Database(str(tmp_path / "test.db"))
        await db.initialize()
        yield db
        await db.close()

    @pytest.fixture
    def project_dir(self, tmp_path) -> Path:
        """Create a project directory with a sample Python file."""
        proj = tmp_path / "project"
        proj.mkdir()
        src = proj / "src"
        src.mkdir()
        (src / "app.py").write_text(
            textwrap.dedent("""\
                import os

                def hello():
                    return "world"
            """)
        )
        # Create a secret file OUTSIDE the project
        (tmp_path / "secret.txt").write_text("TOP SECRET")
        return proj

    async def test_traversal_blocked(self, db, project_dir):
        """Attempting to read ../../secret.txt should be blocked."""
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder

        kg = KnowledgeGraphBuilder(db, project_dir=str(project_dir))
        result = await kg.analyze_file("../secret.txt")
        assert "error" in result
        assert result["nodes"] == 0

    async def test_traversal_via_absolute_path_blocked(self, db, project_dir, tmp_path):
        """Attempting to read an absolute path outside project should be blocked."""
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder

        kg = KnowledgeGraphBuilder(db, project_dir=str(project_dir))
        # Use _safe_read directly
        content = kg._safe_read(str(tmp_path / "secret.txt"))
        assert content is None

    async def test_valid_path_allowed(self, db, project_dir):
        """Reading a file inside the project directory should work."""
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder

        kg = KnowledgeGraphBuilder(db, project_dir=str(project_dir))
        result = await kg.analyze_file("src/app.py")
        assert result["nodes"] > 0
        assert "error" not in result

    async def test_safe_read_nonexistent_file(self, db, project_dir):
        """Reading a nonexistent file returns None."""
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder

        kg = KnowledgeGraphBuilder(db, project_dir=str(project_dir))
        content = kg._safe_read("nonexistent.py")
        assert content is None

    async def test_safe_read_rejects_large_file(self, db, project_dir):
        """Files exceeding MAX_FILE_SIZE should be rejected."""
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder, MAX_FILE_SIZE

        # Create a large file
        large_file = project_dir / "large.py"
        large_file.write_text("x" * (MAX_FILE_SIZE + 1))

        kg = KnowledgeGraphBuilder(db, project_dir=str(project_dir))
        content = kg._safe_read("large.py")
        assert content is None

    async def test_backward_compat_no_project_dir(self, db):
        """Without project_dir, analyze_file still works with source_code provided."""
        from taskbrew.intelligence.knowledge_graph import KnowledgeGraphBuilder

        kg = KnowledgeGraphBuilder(db)
        result = await kg.analyze_file(
            "/fake/sample.py",
            source_code="import os\ndef foo(): pass\n",
        )
        assert result["nodes"] > 0
        assert "error" not in result
