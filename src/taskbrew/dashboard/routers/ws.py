"""WebSocket endpoints.

Audit 10 F#4 / F#7: WebSocket handlers used to call ws.accept()
unconditionally. That left two holes open:

- No Origin validation = cross-site WebSocket hijacking (CSWSH). An
  attacker page could open a WS back to the dashboard using the
  victim's browser credentials and drive chat sessions / read the
  orchestration event stream.
- No bearer token = anyone reachable on the port could connect when
  AUTH_ENABLED=true. The dashboard HTTP middleware skipped ``/ws*``
  entirely.

_ws_accept_or_reject() now runs before accept() and closes both holes.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Injected deps (auth_manager + allowed_origins added in audit 10 F#4)
# ------------------------------------------------------------------
_ws_manager = None
_chat_manager = None
_auth_manager = None
_allowed_origins: list[str] = []
# WS close codes (RFC 6455 + application-defined 4xxx).
_POLICY_VIOLATION = 1008


def set_ws_deps(
    ws_manager,
    chat_manager=None,
    auth_manager=None,
    allowed_origins=None,
):
    """Called by app.py to inject deps.

    *auth_manager* and *allowed_origins* are optional; when omitted,
    WebSocket endpoints degrade to their pre-audit behaviour (no Origin
    check, no bearer check). Real deployments should pass both.
    """
    global _ws_manager, _chat_manager, _auth_manager, _allowed_origins
    _ws_manager = ws_manager
    _chat_manager = chat_manager
    _auth_manager = auth_manager
    _allowed_origins = list(allowed_origins or [])


def _origin_ok(origin: str | None) -> bool:
    """Return True iff *origin* is in the configured allowlist.

    Empty allowlist = permissive (legacy / tests). Missing Origin header
    (curl, same-origin non-browser clients) is allowed because browsers
    always send one; CSWSH by definition needs a different-origin page,
    so a missing Origin is never the attack vector.
    """
    if not _allowed_origins:
        return True
    if not origin:
        return True
    return origin in _allowed_origins


def _extract_token(ws: WebSocket) -> str | None:
    """Pull a bearer token from the WS handshake.

    Two delivery channels (browser JS cannot set Authorization on a WS
    handshake):

    1. ``Sec-WebSocket-Protocol: bearer, <token>``  (preferred; token
       is never logged in request URLs).
    2. ``?token=<token>`` query string  (fallback for clients that
       cannot negotiate subprotocols).
    """
    # Subprotocol channel.
    proto_header = ws.headers.get("sec-websocket-protocol", "") or ""
    protocols = [p.strip() for p in proto_header.split(",") if p.strip()]
    if len(protocols) >= 2 and protocols[0].lower() == "bearer":
        return protocols[1]

    # Query string channel.
    query = ws.scope.get("query_string", b"")
    if isinstance(query, bytes):
        query = query.decode("latin-1")
    params = parse_qs(query)
    token_list = params.get("token") or []
    if token_list:
        return token_list[0]
    return None


async def _ws_accept_or_reject(ws: WebSocket) -> tuple[bool, str | None]:
    """Validate origin + auth, then reject (close) or return (True, subproto).

    Returns (True, chosen_subprotocol_or_None) when the handshake should
    proceed -- caller calls ws.accept(subprotocol=...) via
    ConnectionManager.connect(). Returns (False, None) after calling
    ws.close() on rejection.
    """
    # 1. Origin check (CSWSH defence).
    origin = ws.headers.get("origin")
    if not _origin_ok(origin):
        logger.warning("WS handshake rejected: bad origin %r", origin)
        await ws.close(code=_POLICY_VIOLATION)
        return False, None

    # 2. Bearer check (only when auth is enabled).
    if _auth_manager is not None and getattr(_auth_manager, "enabled", False):
        token = _extract_token(ws)
        if not token or not _auth_manager.verify_token_string(token):
            logger.warning("WS handshake rejected: invalid/missing bearer")
            await ws.close(code=_POLICY_VIOLATION)
            return False, None
        # When the client used subprotocol channel, echo "bearer" back
        # so the browser considers the negotiation successful.
        proto_header = ws.headers.get("sec-websocket-protocol", "") or ""
        if proto_header and "bearer" in proto_header.lower():
            return True, "bearer"
    return True, None


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    ok, subproto = await _ws_accept_or_reject(ws)
    if not ok:
        return
    await _ws_manager.connect(ws, subprotocol=subproto)
    try:
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue  # ignore malformed messages
            if msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await _ws_manager.disconnect(ws)


def register_chat_routes(app, chat_manager):
    """Register chat endpoints on the given FastAPI app.

    These are registered directly on the app because they depend on
    the chat_manager being conditionally available.
    """
    from fastapi import HTTPException
    from taskbrew.agents.roles import get_agent_config
    from taskbrew.dashboard.routers._deps import get_orch_optional

    @app.get("/api/chat/sessions")
    async def get_chat_sessions():
        return {
            name: {
                "session_id": s.session_id,
                "agent_name": s.agent_name,
                "is_connected": s.is_connected,
                "is_responding": s.is_responding,
                "message_count": len(s.history),
            }
            for name, s in chat_manager.sessions.items()
        }

    @app.get("/api/chat/{agent_name}/history")
    async def get_chat_history(agent_name: str):
        history = chat_manager.get_history(agent_name)
        if history is None:
            raise HTTPException(status_code=404, detail=f"No chat session for '{agent_name}'")
        return [{"id": m.id, "role": m.role, "content": m.content, "timestamp": m.timestamp} for m in history]

    @app.delete("/api/chat/{agent_name}")
    async def delete_chat_session(agent_name: str):
        session = chat_manager.get_session(agent_name)
        if not session:
            raise HTTPException(status_code=404, detail=f"No chat session for '{agent_name}'")
        await chat_manager.stop_session(agent_name)
        return {"agent": agent_name, "status": "disconnected"}

    @app.websocket("/ws/chat/{agent_name}")
    async def chat_websocket(ws: WebSocket, agent_name: str):
        """Chat channel for a single agent instance.

        audit 10 F#27: the ``stop_session(agent_name)`` call in
        ``finally`` used to fire on every disconnect regardless of
        which WS connection started the session. A second connection
        to the same agent_name would terminate the first user's
        session on its own disconnect. We now only stop the session
        if this connection is the one that started it.
        """
        ok, subproto = await _ws_accept_or_reject(ws)
        if not ok:
            return
        await ws.accept(subprotocol=subproto)
        _this_connection_started = False
        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    continue  # ignore malformed messages
                msg_type = msg.get("type")

                if msg_type == "start_session":
                    try:
                        # Use YAML-loaded roles from the orchestrator when available,
                        # so that roles like "verifier" (defined only in YAML, not in
                        # the hardcoded AGENT_ROLES dict) are resolved correctly.
                        orch = get_orch_optional()
                        config_roles = getattr(orch, "roles", None) if orch else None
                        # agent_name is an instance id like "pm-1"; the role
                        # config is keyed by the role name ("pm"). Strip the
                        # trailing "-N" suffix when present so the lookup hits.
                        role_lookup = agent_name
                        if config_roles and agent_name not in config_roles:
                            base = agent_name.rsplit("-", 1)[0]
                            if base in config_roles:
                                role_lookup = base
                        config = get_agent_config(role_lookup, config_roles=config_roles)
                        existing = chat_manager.get_session(agent_name)
                        session = await chat_manager.start_session(agent_name, config)
                        # Use object identity to decide ownership.
                        # start_session is idempotent: if it returns
                        # the same object that get_session() returned
                        # before the call, this connection attached to
                        # an existing session (do NOT tear down on
                        # disconnect). If it returns a different object
                        # (None previously, or stale session was
                        # replaced) this connection created the live
                        # session and owns its lifetime.
                        _this_connection_started = (session is not existing)
                        await ws.send_text(json.dumps({
                            "type": "session_started",
                            "agent": agent_name,
                            "session_id": session.session_id,
                        }))
                    except Exception as e:
                        await ws.send_text(json.dumps({
                            "type": "chat_error",
                            "agent": agent_name,
                            "error": str(e),
                        }))

                elif msg_type == "chat_message":
                    content = msg.get("content", "")
                    try:
                        async def on_token(text):
                            try:
                                await ws.send_text(json.dumps({
                                    "type": "chat_token",
                                    "agent": agent_name,
                                    "content": text,
                                }))
                            except Exception:
                                pass  # WebSocket disconnected

                        async def on_tool_use(tool, tool_input):
                            try:
                                await ws.send_text(json.dumps({
                                    "type": "chat_tool_use",
                                    "agent": agent_name,
                                    "tool": tool,
                                    "input": tool_input,
                                }))
                            except Exception:
                                pass  # WebSocket disconnected

                        result = await chat_manager.send_message(
                            agent_name, content,
                            on_token=on_token,
                            on_tool_use=on_tool_use,
                        )
                        await ws.send_text(json.dumps({
                            "type": "chat_response_complete",
                            "agent": agent_name,
                            "content": result,
                        }))
                    except Exception as e:
                        try:
                            await ws.send_text(json.dumps({
                                "type": "chat_error",
                                "agent": agent_name,
                                "error": str(e),
                            }))
                        except Exception:
                            pass  # WebSocket already disconnected

                elif msg_type == "stop_session":
                    await chat_manager.stop_session(agent_name)
                    _this_connection_started = False
                    await ws.send_text(json.dumps({
                        "type": "session_stopped",
                        "agent": agent_name,
                    }))
                    break

        except WebSocketDisconnect:
            # audit 10 F#27: only tear down the session if this
            # connection is the one that started it; otherwise the
            # first user keeps their session when a second WS closes.
            if _this_connection_started:
                await chat_manager.stop_session(agent_name)
