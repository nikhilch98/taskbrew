"""Concurrent project-scoping isolation tests.

These guard the seam that makes "focus one, run many" safe: an agent's MCP
callback carries an ``X-Taskbrew-Project`` header, the ``_project_scope``
middleware binds it to a request-scoped ContextVar, and ``_deps`` resolves the
agent's *own* orchestrator — never the focused one. The failure mode this
exercises (BaseHTTPMiddleware sharing/leaking context across concurrent
requests) silently cross-writes project databases, so it is tested with truly
concurrent in-flight requests, not a single sequential call.

This also pins the behaviour to the *installed* Starlette: if a future bump
regresses ContextVar propagation through ``@app.middleware('http')``, these
tests fail loudly here instead of corrupting data in production.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request

from taskbrew.dashboard.routers import _deps


class _FakeOrch:
    def __init__(self, pid: str) -> None:
        self.project_id = pid


@pytest.fixture(autouse=True)
def _clean_deps():
    """Isolate the module-level orchestrator pool around every test."""
    _deps._orchestrators.clear()
    _deps._focused_id = None
    yield
    _deps._orchestrators.clear()
    _deps._focused_id = None


def _make_app() -> FastAPI:
    """A minimal app wired with the same _project_scope middleware as app.py."""
    app = FastAPI()

    @app.middleware("http")
    async def _project_scope(request: Request, call_next):
        token = _deps.set_current_project(request.headers.get("X-Taskbrew-Project"))
        try:
            return await call_next(request)
        finally:
            _deps.reset_current_project(token)

    @app.get("/whoami")
    async def whoami():
        # Sleep so concurrent requests are genuinely in-flight together; a
        # leaking ContextVar would surface as the wrong pid here.
        await asyncio.sleep(0.05)
        orch = _deps.get_orch_optional()
        return {"pid": getattr(orch, "project_id", None)}

    return app


async def test_concurrent_requests_resolve_their_own_project():
    _deps.register_orchestrator(_FakeOrch("alpha"))
    _deps.register_orchestrator(_FakeOrch("beta"))  # focused = beta (last)

    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        # Fire many interleaved requests so any cross-request context bleed
        # has ample opportunity to manifest.
        reqs = []
        for _ in range(8):
            reqs.append(client.get("/whoami", headers={"X-Taskbrew-Project": "alpha"}))
            reqs.append(client.get("/whoami", headers={"X-Taskbrew-Project": "beta"}))
            reqs.append(client.get("/whoami"))  # no header -> focused
        results = await asyncio.gather(*reqs)

    pids = [r.json()["pid"] for r in results]
    alpha_pids = pids[0::3]
    beta_pids = pids[1::3]
    none_pids = pids[2::3]
    assert all(p == "alpha" for p in alpha_pids), alpha_pids
    assert all(p == "beta" for p in beta_pids), beta_pids
    # No header resolves to the focused project (beta), never bleed from a
    # concurrent alpha request.
    assert all(p == "beta" for p in none_pids), none_pids


async def test_unknown_header_is_failsafe_none():
    """A header naming an unregistered project resolves to None (→ 409), never
    the focused project — so a stale/wrong header can't cross-write."""
    _deps.register_orchestrator(_FakeOrch("alpha"))
    app = _make_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/whoami", headers={"X-Taskbrew-Project": "ghost"})
    assert r.json()["pid"] is None


def test_resolve_routing_unit():
    """Direct unit coverage of _deps._resolve precedence."""
    _deps.register_orchestrator(_FakeOrch("alpha"))
    _deps.register_orchestrator(_FakeOrch("beta"))
    # Focused (no contextvar) -> last registered.
    assert _deps.get_orch_optional().project_id == "beta"
    # Explicit header binds strictly to that project.
    tok = _deps.set_current_project("alpha")
    try:
        assert _deps.get_orch_optional().project_id == "alpha"
    finally:
        _deps.reset_current_project(tok)
    # Unknown header -> None (fail-safe), not the focused project.
    tok = _deps.set_current_project("ghost")
    try:
        assert _deps.get_orch_optional() is None
    finally:
        _deps.reset_current_project(tok)


def test_set_orchestrator_none_drops_focused_keeps_others():
    """set_orchestrator(None) drops only the focused project; others survive."""
    _deps.register_orchestrator(_FakeOrch("alpha"))
    _deps.register_orchestrator(_FakeOrch("beta"))  # focused
    _deps.set_orchestrator(None)
    # beta dropped, alpha remains and becomes the lone (focused) orchestrator.
    assert _deps.get_orch_optional().project_id == "alpha"
    assert set(_deps.get_all_orchestrators()) == {"alpha"}
