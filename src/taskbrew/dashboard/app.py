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
    """WebSocket connection registry and broadcast fan-out.

    audit 10 F#13: append/remove/iterate on ``self.active`` now runs
    under ``self._lock`` so concurrent connect/disconnect cannot leave a
    stale reference in-flight during broadcast.
    """

    def __init__(self):
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, subprotocol: str | None = None):
        # Caller must have already validated origin + auth. subprotocol
        # is echoed back so browser clients can confirm negotiation.
        await ws.accept(subprotocol=subprotocol)
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            try:
                self.active.remove(ws)
            except ValueError:
                pass  # already removed

    async def broadcast(self, data: dict[str, Any]):
        message = json.dumps(data)
        # Snapshot under the lock so a disconnect mid-iteration doesn't
        # mutate the list we're iterating.
        async with self._lock:
            targets = list(self.active)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
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
    # audit 01 F#10 / 17 F#2 / 18 F#2: source version from importlib.metadata
    # (see src/taskbrew/__init__.py) so pyproject.toml is the single source
    # of truth. Endpoint count is computed after include_router() below
    # and appended to the OpenAPI description to replace the old hand-
    # maintained "89+ API endpoints" string which drifted to ~426.
    from taskbrew import __version__ as _taskbrew_version

    app = FastAPI(
        title="TaskBrew Dashboard",
        description="Multi-agent AI team orchestrator.",
        version=_taskbrew_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    from starlette.middleware.cors import CORSMiddleware
    raw_cors = os.environ.get(
        "CORS_ORIGINS", "http://localhost:8000,http://localhost:3000"
    )
    cors_origins = [origin.strip() for origin in raw_cors.split(",") if origin.strip()]
    # audit 10 F#8: refuse the ``*`` wildcard when we ship
    # allow_credentials=True. Browsers reject this combination anyway,
    # but a mis-configured ``CORS_ORIGINS=*`` would previously run in
    # "permissive" mode at server boot with no warning; hard-fail with
    # a clear message so operators catch it at startup.
    if any(origin == "*" for origin in cors_origins):
        raise RuntimeError(
            "CORS_ORIGINS cannot contain '*' while allow_credentials=True. "
            "Either list the explicit origins you trust, or clear "
            "CORS_ORIGINS for dev defaults (http://localhost:8000,3000)."
        )
    for origin in cors_origins:
        if not (origin.startswith("http://") or origin.startswith("https://")):
            raise RuntimeError(
                f"CORS_ORIGINS entry {origin!r} missing http(s):// scheme"
            )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # ------------------------------------------------------------------
    # Auth dependency (env-var driven, fail-closed by default)
    # audit 10 F#1/F#3/F#4: AUTH_ENABLED defaults to True in production.
    # tests/conftest.py sets AUTH_ENABLED=false at import time to keep
    # the existing test suite passing unchanged.
    # ------------------------------------------------------------------
    _auth_env = os.environ.get("AUTH_ENABLED")
    if _auth_env is None:
        _auth_enabled = True
        _logger.warning(
            "AUTH_ENABLED unset — defaulting to True (fail-closed). "
            "Set AUTH_ENABLED=false explicitly for local development."
        )
    else:
        _auth_enabled = _auth_env.lower() == "true"

    _auth_manager = AuthManager(enabled=_auth_enabled)
    if not _auth_enabled:
        _logger.info(
            "API authentication is disabled (AUTH_ENABLED=%s). Production "
            "deployments should unset AUTH_ENABLED or set it to true.",
            _auth_env,
        )

    # Auth middleware
    tc = team_config

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # Skip auth for pages, health, websocket, and static.
        # WebSocket auth hardening is tracked in audit 10 F#4/F#7 and is
        # deferred from this fix.
        skip_paths = {
            "/", "/metrics", "/settings", "/costs", "/trace", "/questions",
            "/api/health", "/docs", "/redoc", "/openapi.json",
        }
        if (
            request.url.path in skip_paths
            or request.url.path.startswith("/ws")
            or request.url.path.startswith("/static")
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        # Resolve the active team_config (may arrive via project_manager).
        _tc = tc
        if not _tc and project_manager and project_manager.orchestrator:
            _tc = project_manager.orchestrator.team_config

        # FAIL-CLOSED POLICY (audit 10 F#3):
        # The gate is "ANY of the configured auth surfaces demands a token".
        # If the env-var AuthManager is enabled OR the team_config demands
        # auth_enabled, we require a valid bearer. The previous code
        # short-circuited to "no team_config -> pass-through", which was
        # the dominant deployment path.
        team_requires = bool(_tc and _tc.auth_enabled)
        env_requires = bool(_auth_manager.enabled)
        if not (team_requires or env_requires):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                {"error": "Missing or invalid Authorization header"},
                status_code=401,
            )
        token = auth_header[7:]
        if not token:
            return JSONResponse({"error": "Empty bearer token"}, status_code=401)

        # Accept if EITHER the env-var AuthManager recognises the token OR
        # it appears in team_config.auth_tokens. Using hmac.compare_digest
        # below is a defense-in-depth vs. timing leaks.
        env_ok = env_requires and _auth_manager.verify_token_string(token)
        team_ok = False
        if team_requires and _tc and _tc.auth_tokens:
            import hmac as _hmac
            # isinstance guard: YAML scalars that look like numbers are
            # parsed as int by PyYAML unless quoted. hmac.compare_digest
            # TypeErrors on mixed types, so silently skip non-string
            # candidates. (Operators should quote tokens in team.yaml.)
            team_ok = any(
                isinstance(candidate, str)
                and _hmac.compare_digest(token, candidate)
                for candidate in _tc.auth_tokens
            )
        if not (env_ok or team_ok):
            return JSONResponse({"error": "Invalid token"}, status_code=401)

        return await call_next(request)

    async def verify_auth(request: Request):
        """Auth dependency that consults both env-var and team_config surfaces.

        Fail-closed whenever either surface demands a bearer.
        """
        _tc = tc
        if not _tc and project_manager and project_manager.orchestrator:
            _tc = project_manager.orchestrator.team_config

        team_requires = bool(_tc and _tc.auth_enabled)
        env_requires = bool(_auth_manager.enabled)
        if not (team_requires or env_requires):
            return  # No auth surface demands a token

        auth_header = request.headers.get("Authorization", "") if request else ""
        token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

        if not token:
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

        env_ok = env_requires and _auth_manager.verify_token_string(token)
        team_ok = False
        if team_requires and _tc and _tc.auth_tokens:
            import hmac as _hmac
            team_ok = any(
                isinstance(candidate, str)
                and _hmac.compare_digest(token, candidate)
                for candidate in _tc.auth_tokens
            )
        if not (env_ok or team_ok):
            raise HTTPException(status_code=401, detail="Invalid token")

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

    # Initialize pipeline config from team.yaml or auto-migrate
    from taskbrew.dashboard.routers.pipeline_editor import set_pipeline_deps
    from taskbrew.config_loader import load_pipeline, migrate_routes_to_pipeline, save_pipeline as _save_pipeline
    if orch_obj and getattr(orch_obj, "project_dir", None):
        team_yaml_path = Path(orch_obj.project_dir) / "config" / "team.yaml"
        if team_yaml_path.exists():
            pc = load_pipeline(team_yaml_path)
            if not pc.edges and orch_obj.roles:
                # Auto-migrate from routes_to
                pc = migrate_routes_to_pipeline(orch_obj.roles)
                if pc.edges:
                    _save_pipeline(team_yaml_path, pc)
            set_pipeline_deps(pc)

    system_router.set_auth_deps(verify_admin)
    system_router.set_project_deps(project_manager, broadcast_event)
    comparison_router.set_comparison_deps(project_manager)
    # audit 10 F#4/F#7: wire auth_manager + cors_origins into the WS
    # router so it can validate the Origin header and the bearer token
    # on handshake.
    ws_router.set_ws_deps(
        ws_manager,
        chat_manager,
        auth_manager=_auth_manager,
        allowed_origins=cors_origins,
    )

    # audit 11a F#16: gate the /api/usage/* CLI-spawning endpoints.
    from taskbrew.dashboard.routers import usage as usage_router
    usage_router.set_usage_auth_deps(verify_admin)

    # Wire up interaction and MCP tool dependencies
    if orch_obj:
        from taskbrew.orchestrator.interactions import InteractionManager
        from taskbrew.dashboard.routers.mcp_tools import set_mcp_deps
        from taskbrew.dashboard.routers.interactions import set_interaction_deps
        from taskbrew.dashboard.routers.pipeline_editor import get_pipeline
        interaction_mgr = InteractionManager(orch_obj.task_board._db)
        set_interaction_deps(interaction_mgr, verify_admin=verify_admin)
        # Capture orchestrator via a getter (not the value) so a future
        # activate_project swap is visible to the MCP layer without
        # re-wiring deps.
        from taskbrew.dashboard.routers._deps import get_orch_optional
        set_mcp_deps(
            interaction_mgr,
            get_pipeline,
            orch_obj.task_board,
            auth_manager=_auth_manager,
            event_bus=orch_obj.event_bus,
            orchestrator_getter=get_orch_optional,
        )

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
    from taskbrew.dashboard.routers import presets as presets_router
    from taskbrew.dashboard.routers.pipeline_editor import router as pipeline_editor_router
    from taskbrew.dashboard.routers.mcp_tools import router as mcp_tools_router
    from taskbrew.dashboard.routers.interactions import router as interactions_router

    app.include_router(tasks_router, tags=["Tasks"])
    # audit 10 F#28: /api/agents/pause and /api/agents/resume mutate
    # running agent state; they need admin auth at include time for
    # belt-and-suspenders with the middleware gate.
    app.include_router(
        agents_router,
        tags=["Agents"],
        dependencies=[Depends(verify_admin)],
    )

    # audit 14 F#1 + 10 F#12: emit a conservative set of security
    # response headers on every request. CSP is declared
    # permissively enough to keep the existing inline <script>
    # blocks working (unsafe-inline) while blocking framing and
    # MIME sniffing. When the inline scripts are extracted to
    # /static/js/*.js the CSP should drop 'unsafe-inline' from
    # script-src.
    _SECURITY_HEADERS = {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self'"
        ),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    # audit 12a / cross-cutting: v1 and v2 Intelligence routers are
    # deprecated in favour of v3. We add a middleware that stamps
    # ``Deprecation: true`` / ``Sunset`` headers on every v1+v2
    # response so HTTP clients (dashboard, operator scripts, CI)
    # can see the deprecation without removing the routes yet.
    # Pick a conservative sunset date: 12 months from the current
    # version's release -- operators have a year to migrate.
    from datetime import datetime, timezone, timedelta
    _intel_sunset = (
        datetime.now(timezone.utc) + timedelta(days=365)
    ).strftime("%a, %d %b %Y %H:%M:%S GMT")

    @app.middleware("http")
    async def _intel_deprecation_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/v2/") or (
            path.startswith("/api/")
            and not path.startswith("/api/v2/")
            and not path.startswith("/api/v3/")
            and "intelligence" in path
        ):
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = _intel_sunset
            response.headers["Link"] = (
                '</api/v3>; rel="successor-version"'
            )
        return response

    # audit 12a F#1 / 12b F#1: the intel v1/v2/v3 routers mutate
    # prediction / analysis tables and, for v3, fan out to the
    # various intelligence managers that issue tool calls. The
    # global AUTH_ENABLED middleware already gates these paths,
    # but belt-and-suspenders: bind Depends(verify_admin) at
    # include time so they fail closed even if middleware is
    # later weakened.
    app.include_router(
        intelligence_router,
        tags=["Intelligence V1 (deprecated)"],
        dependencies=[Depends(verify_admin)],
    )
    app.include_router(
        intelligence_v2_router,
        tags=["Intelligence V2 (deprecated)"],
        dependencies=[Depends(verify_admin)],
    )
    app.include_router(
        intelligence_v3_router,
        tags=["Intelligence V3"],
        dependencies=[Depends(verify_admin)],
    )
    app.include_router(costs_router, tags=["Costs"])
    app.include_router(exports_router, tags=["Export & Reports"])
    app.include_router(search_router, tags=["Search"])
    # audit 11a F#15: git endpoints expose the full commit log, the
    # working-tree diff, and the staged diff. A credentialed
    # middleware already fails closed on every route when
    # AUTH_ENABLED is set, but belt-and-suspenders: bind an explicit
    # verify_admin dep at include time so these routes refuse to
    # serve even if the middleware is ever removed.
    app.include_router(
        git_router,
        tags=["Git"],
        dependencies=[Depends(verify_admin)],
    )
    app.include_router(analytics_router, tags=["Analytics"])
    app.include_router(pipelines_router, tags=["Pipelines"])
    app.include_router(comparison_router_obj, tags=["Comparison"])
    app.include_router(collaboration_router, tags=["Collaboration"])
    app.include_router(usage_router, tags=["Usage"])
    app.include_router(presets_router.router, tags=["Presets"])
    # audit 11b F#1: admin routers get an explicit verify_admin dep at
    # include time. This is belt-and-suspenders with the middleware gate
    # above and hard-fails any unauthed mutation on these surfaces even
    # if the middleware is ever disabled by future refactoring.
    app.include_router(
        pipeline_editor_router,
        tags=["Pipeline Editor"],
        dependencies=[Depends(verify_admin)],
    )
    app.include_router(mcp_tools_router, tags=["MCP Tools"])
    # interactions_router splits auth per-endpoint: the four POST routes
    # (approve/reject/respond/skip) carry Depends(_verify_admin_dep)
    # inline; the two GET polling routes stay open so UI polling does not
    # require a token under default configuration. This was flipped from
    # a blanket include-time dep after review identified the polling
    # regression.
    app.include_router(interactions_router, tags=["Interactions"])
    app.include_router(
        system_router.router,
        tags=["System"],
        dependencies=[Depends(verify_admin)],
    )
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

    @app.get("/questions")
    async def questions_page(request: Request):
        """Standalone pending-questions view.

        Lists every pending agent_questions row with the agent's
        recommendation collapsed by default; lets the human pick an
        option and submit. Cancel-this-task button calls the existing
        cancel endpoint.
        """
        return templates.TemplateResponse(request, "questions.html")

    @app.get("/trace")
    async def trace_page(request: Request):
        """Standalone execution-trace view.

        Bookmarkable URL: ``/trace?group_id=FEAT-001``. Fetches
        ``/api/groups/{id}/trace`` client-side and renders the
        per-task timing / cost / verification grid plus group
        aggregates. Self-contained — doesn't touch the rest of
        the dashboard's UI surface.
        """
        return templates.TemplateResponse(request, "trace.html")



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
