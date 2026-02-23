"""Integration tests: auth middleware.

Verifies the AUTH_ENABLED environment variable correctly controls
whether API endpoints require Bearer token authentication.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from taskbrew.auth import AuthManager
from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.agents.instance_manager import InstanceManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


async def _make_app_and_db(tmp_path, auth_enabled: bool = False):
    """Create a FastAPI app with the given auth setting."""
    db = Database(str(tmp_path / "auth_test.db"))
    await db.initialize()
    board = TaskBoard(db, group_prefixes={"pm": "FEAT"})
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    event_bus = EventBus()
    instance_mgr = InstanceManager(db)

    env_val = "true" if auth_enabled else "false"
    with patch.dict(os.environ, {"AUTH_ENABLED": env_val}):
        from taskbrew.dashboard.app import create_app

        app = create_app(
            event_bus=event_bus,
            task_board=board,
            instance_manager=instance_mgr,
        )

    return app, db


def _find_auth_manager(app) -> AuthManager | None:
    """Extract the AuthManager from the verify_admin closure on the restart route."""
    for route in app.routes:
        if hasattr(route, "path") and route.path == "/api/server/restart":
            for dep in route.dependencies:
                fn = dep.dependency
                if hasattr(fn, "__code__") and fn.__closure__:
                    for cell in fn.__closure__:
                        val = cell.cell_contents
                        if callable(val) and hasattr(val, "__name__") and val.__name__ == "verify_auth":
                            if val.__closure__:
                                for inner_cell in val.__closure__:
                                    inner_val = inner_cell.cell_contents
                                    if isinstance(inner_val, AuthManager):
                                        return inner_val
            break
    return None


@pytest.fixture
async def auth_client(tmp_path):
    """Client with AUTH_ENABLED=true."""
    app, db = await _make_app_and_db(tmp_path, auth_enabled=True)
    auth_mgr = _find_auth_manager(app)
    token = auth_mgr.generate_token() if auth_mgr else None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield {"client": c, "token": token, "auth_mgr": auth_mgr}
    await db.close()


@pytest.fixture
async def noauth_client(tmp_path):
    """Client with AUTH_ENABLED=false (default)."""
    app, db = await _make_app_and_db(tmp_path, auth_enabled=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ------------------------------------------------------------------
# Tests: auth enabled
# ------------------------------------------------------------------


class TestAuthEnabled:
    """When AUTH_ENABLED=true, protected endpoints require a valid Bearer token."""

    async def test_unauthenticated_request_blocked(self, auth_client):
        """GET /api/server/restart without token returns 401."""
        resp = await auth_client["client"].post("/api/server/restart")
        assert resp.status_code == 401

    async def test_authenticated_request_allowed(self, auth_client):
        """GET /api/server/restart with valid Bearer token returns 200."""
        token = auth_client["token"]
        assert token is not None, "AuthManager should have generated a token"
        resp = await auth_client["client"].post(
            "/api/server/restart",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    async def test_invalid_token_rejected(self, auth_client):
        """Request with a bad token returns 401."""
        resp = await auth_client["client"].post(
            "/api/server/restart",
            headers={"Authorization": "Bearer totally-invalid-token-12345"},
        )
        assert resp.status_code == 401

    async def test_missing_bearer_prefix_rejected(self, auth_client):
        """Token without 'Bearer ' prefix returns 401."""
        token = auth_client["token"]
        resp = await auth_client["client"].post(
            "/api/server/restart",
            headers={"Authorization": token},
        )
        assert resp.status_code == 401

    async def test_revoked_token_rejected(self, auth_client):
        """A token that was valid but then revoked should be rejected."""
        auth_mgr = auth_client["auth_mgr"]
        token = auth_mgr.generate_token()

        # Verify it works
        resp = await auth_client["client"].post(
            "/api/server/restart",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Revoke it
        auth_mgr.revoke_token(token)

        # Verify it no longer works
        resp = await auth_client["client"].post(
            "/api/server/restart",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


# ------------------------------------------------------------------
# Tests: auth disabled
# ------------------------------------------------------------------


class TestAuthDisabled:
    """When AUTH_ENABLED=false, all endpoints should be accessible."""

    async def test_health_accessible(self, noauth_client):
        resp = await noauth_client.get("/api/health")
        assert resp.status_code == 200

    async def test_restart_accessible(self, noauth_client):
        """Even the admin-only restart endpoint should be accessible without auth."""
        resp = await noauth_client.post("/api/server/restart")
        assert resp.status_code == 200


# ------------------------------------------------------------------
# Tests: static/template pages bypass auth
# ------------------------------------------------------------------


class TestStaticBypassAuth:
    """Template pages should be accessible even when auth is enabled."""

    async def test_index_accessible_without_auth(self, auth_client):
        """GET / (index template) should not require authentication."""
        resp = await auth_client["client"].get("/")
        assert resp.status_code == 200

    async def test_metrics_accessible_without_auth(self, auth_client):
        """GET /metrics should not require authentication."""
        resp = await auth_client["client"].get("/metrics")
        assert resp.status_code == 200

    async def test_settings_accessible_without_auth(self, auth_client):
        """GET /settings should not require authentication."""
        resp = await auth_client["client"].get("/settings")
        assert resp.status_code == 200

    async def test_health_accessible_without_auth(self, auth_client):
        """GET /api/health should not require authentication."""
        resp = await auth_client["client"].get("/api/health")
        assert resp.status_code == 200
