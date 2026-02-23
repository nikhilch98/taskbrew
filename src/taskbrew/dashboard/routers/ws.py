"""WebSocket endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# ------------------------------------------------------------------
# Connection manager and chat_manager are injected by app.py
# ------------------------------------------------------------------
_ws_manager = None
_chat_manager = None


def set_ws_deps(ws_manager, chat_manager=None):
    """Called by app.py to inject the WebSocket connection manager."""
    global _ws_manager, _chat_manager
    _ws_manager = ws_manager
    _chat_manager = chat_manager


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await _ws_manager.connect(ws)
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
        _ws_manager.disconnect(ws)


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
        await ws.accept()
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
                        config = get_agent_config(agent_name, config_roles=config_roles)
                        session = await chat_manager.start_session(agent_name, config)
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
                    await ws.send_text(json.dumps({
                        "type": "session_stopped",
                        "agent": agent_name,
                    }))
                    break

        except WebSocketDisconnect:
            await chat_manager.stop_session(agent_name)
