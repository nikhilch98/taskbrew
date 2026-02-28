"""FastAPI dashboard backend with WebSocket support."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from starlette.requests import Request

from taskbrew.auth import AuthManager
from taskbrew.config_loader import RoleConfig, TeamConfig
from taskbrew.orchestrator.event_bus import EventBus
from taskbrew.orchestrator.task_board import TaskBoard
from taskbrew.agents.instance_manager import InstanceManager

if TYPE_CHECKING:
    from taskbrew.dashboard.chat_manager import ChatManager

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# ConnectionManager (used by WebSocket router)
# ------------------------------------------------------------------


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        try:
            self.active.remove(ws)
        except ValueError:
            pass  # already removed

    async def broadcast(self, data: dict[str, Any]):
        message = json.dumps(data)
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                self.active.remove(ws)
            except ValueError:
                pass


def create_app(
    project_manager=None,
    # Keep old params for backward compat in tests
    event_bus: EventBus | None = None,
    task_board: TaskBoard | None = None,
    instance_manager: InstanceManager | None = None,
    chat_manager: ChatManager | None = None,
    roles: dict[str, RoleConfig] | None = None,
    team_config: TeamConfig | None = None,
    project_dir: str | None = None,
) -> FastAPI:
    app = FastAPI(
        title="TaskBrew Dashboard",
        description="Multi-agent AI team orchestrator with 89+ API endpoints for task management, intelligence features, and team coordination.",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    from starlette.middleware.cors import CORSMiddleware
    cors_origins = [
        origin.strip()
        for origin in os.environ.get(
            "CORS_ORIGINS", "http://localhost:8000,http://localhost:3000"
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # Auth middleware
    tc = team_config

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Skip auth for pages, health, websocket, and static
        _tc = tc
        if not _tc and project_manager and project_manager.orchestrator:
            _tc = project_manager.orchestrator.team_config
        skip_paths = {"/", "/metrics", "/settings", "/api/health", "/docs", "/redoc", "/openapi.json"}
        if (
            not _tc
            or not _tc.auth_enabled
            or request.url.path in skip_paths
            or request.url.path.startswith("/ws")
            or request.url.path.startswith("/static")
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"error": "Missing or invalid Authorization header"}, status_code=401)

        token = auth_header[7:]
        if token not in (_tc.auth_tokens or []):
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        return await call_next(request)

    # ------------------------------------------------------------------
    # Auth dependency (env-var driven, separate from team_config auth)
    # ------------------------------------------------------------------
    _auth_enabled = os.environ.get("AUTH_ENABLED", "false").lower() == "true"
    _auth_manager = AuthManager(enabled=_auth_enabled)
    if not _auth_enabled:
        _logger.info("API authentication is disabled (set AUTH_ENABLED=true to enable)")

    async def verify_auth(request: Request):
        """Optional auth dependency -- disabled by default for development."""
        if not _auth_manager.enabled:
            return  # Auth disabled
        if not _auth_manager.verify(request):
            raise HTTPException(status_code=401, detail="Invalid or missing token")

    async def verify_admin(request: Request):
        """Admin-only endpoint protection."""
        await verify_auth(request)
        # Additional admin check could go here

    ws_manager = ConnectionManager()

    async def broadcast_event(event: dict):
        await ws_manager.broadcast(event)

    # Subscribe to events - will be re-subscribed on project switch
    if event_bus:
        event_bus.subscribe("*", broadcast_event)
    elif project_manager and project_manager.orchestrator:
        project_manager.orchestrator.event_bus.subscribe("*", broadcast_event)

    # ------------------------------------------------------------------
    # Server restart (kept here for auth dependency introspection by tests)
    # ------------------------------------------------------------------

    @app.post("/api/server/restart", dependencies=[Depends(verify_admin)])
    async def restart_server():
        import signal

        async def _delayed_restart():
            await asyncio.sleep(1)
            os.kill(os.getpid(), signal.SIGTERM)

        asyncio.create_task(_delayed_restart())
        return {"status": "restarting"}

    # ------------------------------------------------------------------
    # Build orchestrator reference for routers
    # ------------------------------------------------------------------

    def _build_orch():
        """Build or return orchestrator-like object for routers."""
        if project_manager and project_manager.orchestrator:
            return project_manager.orchestrator
        # Backward compat: tests pass components directly
        if task_board is not None:
            class _FakeOrch:
                pass
            fake = _FakeOrch()
            fake.task_board = task_board
            fake.event_bus = event_bus
            fake.instance_manager = instance_manager
            fake.roles = roles or {}
            fake.team_config = team_config
            fake.project_dir = project_dir
            # Intelligence managers are None when using legacy compat path
            fake.quality_manager = None
            fake.collaboration_manager = None
            fake.specialization_manager = None
            fake.planning_manager = None
            fake.preflight_checker = None
            fake.impact_analyzer = None
            fake.escalation_manager = None
            fake.checkpoint_manager = None
            fake.messaging_manager = None
            fake.knowledge_graph = None
            fake.review_learning = None
            fake.tool_router = None
            fake.memory_manager = None
            fake.context_registry = None
            fake.autonomous_manager = None
            fake.code_intel_manager = None
            fake.learning_manager = None
            fake.coordination_manager = None
            fake.testing_quality_manager = None
            fake.security_intel_manager = None
            fake.observability_manager = None
            fake.advanced_planning_manager = None
            fake.self_improvement_manager = None
            fake.social_intelligence_manager = None
            fake.code_reasoning_manager = None
            fake.task_intelligence_manager = None
            fake.verification_manager = None
            fake.process_intelligence_manager = None
            fake.knowledge_manager = None
            fake.compliance_manager = None
            fake.plugin_registry = None
            fake.db = None
            return fake
        return None

    # ------------------------------------------------------------------
    # Wire up shared deps for routers
    # ------------------------------------------------------------------
    from taskbrew.dashboard.routers._deps import set_orchestrator
    from taskbrew.dashboard.routers import system as system_router
    from taskbrew.dashboard.routers import ws as ws_router
    from taskbrew.dashboard.routers import comparison as comparison_router

    orch_obj = _build_orch()
    if orch_obj is not None:
        set_orchestrator(orch_obj)

    system_router.set_auth_deps(verify_admin)
    system_router.set_project_deps(project_manager, broadcast_event)
    comparison_router.set_comparison_deps(project_manager)
    ws_router.set_ws_deps(ws_manager, chat_manager)

    # ------------------------------------------------------------------
    # Include routers
    # ------------------------------------------------------------------
    from taskbrew.dashboard.routers.tasks import router as tasks_router
    from taskbrew.dashboard.routers.agents import router as agents_router
    from taskbrew.dashboard.routers.intelligence import router as intelligence_router
    from taskbrew.dashboard.routers.intelligence_v2 import router as intelligence_v2_router
    from taskbrew.dashboard.routers.intelligence_v3 import router as intelligence_v3_router
    from taskbrew.dashboard.routers.costs import router as costs_router
    from taskbrew.dashboard.routers.exports import router as exports_router
    from taskbrew.dashboard.routers.search import router as search_router
    from taskbrew.dashboard.routers.git import router as git_router
    from taskbrew.dashboard.routers.analytics import router as analytics_router
    from taskbrew.dashboard.routers.pipelines import router as pipelines_router
    from taskbrew.dashboard.routers.comparison import router as comparison_router_obj
    from taskbrew.dashboard.routers.collaboration import router as collaboration_router
    from taskbrew.dashboard.routers.usage import router as usage_router

    app.include_router(tasks_router, tags=["Tasks"])
    app.include_router(agents_router, tags=["Agents"])
    app.include_router(intelligence_router, tags=["Intelligence V1"])
    app.include_router(intelligence_v2_router, tags=["Intelligence V2"])
    app.include_router(intelligence_v3_router, tags=["Intelligence V3"])
    app.include_router(costs_router, tags=["Costs"])
    app.include_router(exports_router, tags=["Export & Reports"])
    app.include_router(search_router, tags=["Search"])
    app.include_router(git_router, tags=["Git"])
    app.include_router(analytics_router, tags=["Analytics"])
    app.include_router(pipelines_router, tags=["Pipelines"])
    app.include_router(comparison_router_obj, tags=["Comparison"])
    app.include_router(collaboration_router, tags=["Collaboration"])
    app.include_router(usage_router, tags=["Usage"])
    app.include_router(system_router.router, tags=["System"])
    app.include_router(ws_router.router)

    # ------------------------------------------------------------------
    # Chat endpoints (conditional on chat_manager)
    # ------------------------------------------------------------------
    if chat_manager:
        ws_router.register_chat_routes(app, chat_manager)

    # ------------------------------------------------------------------
    # Template rendering
    # ------------------------------------------------------------------

    # Mount static files (CSS/JS extracted from templates)
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/metrics")
    async def metrics_page(request: Request):
        return templates.TemplateResponse(request, "metrics.html")

    @app.get("/settings")
    async def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html")

    @app.get("/costs")
    async def costs_page(request: Request):
        return templates.TemplateResponse(request, "costs.html")



    # ------------------------------------------------------------------
    # API v1 versioned routes (thin delegates)
    # ------------------------------------------------------------------

    v1 = APIRouter(prefix="/api/v1")

    from taskbrew.dashboard.routers.tasks import (
        health as _health,
        get_board as _get_board,
        search_tasks as _search_tasks,
        get_usage as _get_usage,
        get_project_info as _get_project_info,
    )
    from taskbrew.dashboard.routers.agents import get_agents as _get_agents

    @v1.get("/health")
    async def v1_health():
        return await _health()

    @v1.get("/board")
    async def v1_board(
        group_id: str | None = None,
        assigned_to: str | None = None,
        claimed_by: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
    ):
        return await _get_board(
            group_id=group_id, assigned_to=assigned_to,
            claimed_by=claimed_by, task_type=task_type,
            priority=priority,
        )

    @v1.get("/tasks/search")
    async def v1_search_tasks(
        q: str = "",
        group_id: str | None = None,
        status: str | None = None,
        assigned_to: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        return await _search_tasks(
            q=q, group_id=group_id, status=status,
            assigned_to=assigned_to, task_type=task_type,
            priority=priority, limit=limit, offset=offset,
        )

    @v1.get("/agents")
    async def v1_agents():
        return await _get_agents()

    @v1.get("/usage")
    async def v1_usage():
        return await _get_usage()

    @v1.get("/project")
    async def v1_project():
        return await _get_project_info()

    app.include_router(v1)

    return app
