"""FastAPI dashboard backend with WebSocket support."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request

import yaml

from ai_team.config_loader import RoleConfig, TeamConfig
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.task_board import TaskBoard
from ai_team.agents.instance_manager import InstanceManager

if TYPE_CHECKING:
    from ai_team.dashboard.chat_manager import ChatManager


class CreateTaskBody(BaseModel):
    group_id: str
    title: str
    assigned_to: str
    assigned_by: str
    task_type: str
    description: Optional[str] = None
    priority: str = "medium"
    parent_id: Optional[str] = None
    blocked_by: Optional[list[str]] = None


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, data: dict[str, Any]):
        message = json.dumps(data)
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                self.active.remove(ws)


def create_app(
    event_bus: EventBus,
    task_board: TaskBoard,
    instance_manager: InstanceManager,
    chat_manager: ChatManager | None = None,
    roles: dict[str, RoleConfig] | None = None,
    team_config: TeamConfig | None = None,
    project_dir: str | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Team Dashboard")
    ws_manager = ConnectionManager()

    async def broadcast_event(event: dict):
        await ws_manager.broadcast(event)

    event_bus.subscribe("*", broadcast_event)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Task board endpoints
    # ------------------------------------------------------------------

    @app.get("/api/board")
    async def get_board(
        group_id: str | None = None,
        assigned_to: str | None = None,
        claimed_by: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
    ):
        return await task_board.get_board(
            group_id=group_id,
            assigned_to=assigned_to,
            claimed_by=claimed_by,
            task_type=task_type,
            priority=priority,
        )

    @app.get("/api/groups")
    async def get_groups(status: str | None = None):
        return await task_board.get_groups(status=status)

    @app.get("/api/groups/{group_id}/graph")
    async def get_group_graph(group_id: str):
        tasks = await task_board.get_group_tasks(group_id)
        nodes = []
        edges = []
        for task in tasks:
            nodes.append({
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "assigned_to": task["assigned_to"],
                "claimed_by": task.get("claimed_by"),
                "task_type": task["task_type"],
            })
            if task.get("parent_id"):
                edges.append({
                    "from": task["parent_id"],
                    "to": task["id"],
                    "type": "parent",
                })
        # Also add blocked_by edges from task_dependencies
        task_ids = [t["id"] for t in tasks]
        if task_ids:
            placeholders = ",".join("?" * len(task_ids))
            deps = await task_board._db.execute_fetchall(
                f"SELECT task_id, blocked_by FROM task_dependencies WHERE task_id IN ({placeholders})",
                tuple(task_ids),
            )
            for dep in deps:
                edges.append({
                    "from": dep["blocked_by"],
                    "to": dep["task_id"],
                    "type": "blocked_by",
                })
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    @app.post("/api/goals")
    async def submit_goal(body: dict):
        title = body.get("title", "")
        description = body.get("description", "")
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        group = await task_board.create_group(
            title=title, origin="pm", created_by="human",
        )
        task = await task_board.create_task(
            group_id=group["id"],
            title=f"Create PRD: {title}",
            description=description,
            task_type="goal",
            assigned_to="pm",
            created_by="human",
            priority="high",
        )
        await event_bus.emit("group.created", {"group_id": group["id"], "title": title})
        await event_bus.emit("task.created", {"task_id": task["id"], "group_id": group["id"]})
        return {"group_id": group["id"], "task_id": task["id"]}

    @app.post("/api/tasks")
    async def create_task(body: CreateTaskBody):
        task = await task_board.create_task(
            group_id=body.group_id,
            title=body.title,
            task_type=body.task_type,
            assigned_to=body.assigned_to,
            created_by=body.assigned_by,
            description=body.description,
            priority=body.priority,
            parent_id=body.parent_id,
            blocked_by=body.blocked_by,
        )
        await event_bus.emit("task.created", {"task_id": task["id"], "group_id": body.group_id})
        return task

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    @app.get("/api/agents")
    async def get_agents():
        return await instance_manager.get_all_instances()

    # ------------------------------------------------------------------
    # Pause / Resume
    # ------------------------------------------------------------------

    @app.post("/api/agents/pause")
    async def pause_agents(body: dict):
        role = body.get("role")
        if role == "all":
            all_roles = list(roles.keys()) if roles else []
            instance_manager.pause_all(all_roles)
            await event_bus.emit("team.paused", {"roles": all_roles})
            return {"status": "ok", "paused": all_roles}
        elif role:
            instance_manager.pause_role(role)
            await event_bus.emit("role.paused", {"role": role})
            return {"status": "ok", "paused": [role]}
        raise HTTPException(status_code=400, detail="role is required")

    @app.post("/api/agents/resume")
    async def resume_agents(body: dict):
        role = body.get("role")
        if role == "all":
            instance_manager.resume_all()
            await event_bus.emit("team.resumed", {})
            return {"status": "ok", "resumed": "all"}
        elif role:
            instance_manager.resume_role(role)
            await event_bus.emit("role.resumed", {"role": role})
            return {"status": "ok", "resumed": [role]}
        raise HTTPException(status_code=400, detail="role is required")

    @app.get("/api/agents/paused")
    async def get_paused():
        return {"paused_roles": instance_manager.get_paused_roles()}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    @app.get("/api/settings/team")
    async def get_team_settings():
        if not team_config:
            return {}
        return {
            "name": team_config.name,
            "project_dir": project_dir,
            "default_model": getattr(team_config, "default_model", ""),
            "db_path": team_config.db_path,
            "dashboard_host": team_config.dashboard_host,
            "dashboard_port": team_config.dashboard_port,
            "default_poll_interval": team_config.default_poll_interval,
        }

    @app.put("/api/settings/team")
    async def update_team_settings(body: dict):
        if not team_config:
            raise HTTPException(status_code=404, detail="No team config loaded")
        if "name" in body:
            team_config.name = body["name"]
        if "default_model" in body:
            team_config.default_model = body["default_model"]
        return {"status": "ok"}

    @app.get("/api/settings/roles")
    async def get_roles_settings():
        if not roles:
            return []
        result = []
        for name, rc in roles.items():
            result.append({
                "role": name,
                "system_prompt": rc.system_prompt,
                "model": getattr(rc, "model", ""),
                "allowed_tools": rc.allowed_tools,
                "max_instances": rc.max_instances,
                "poll_interval": getattr(rc, "poll_interval", 5),
            })
        return result

    @app.put("/api/settings/roles/{role_name}")
    async def update_role_settings(role_name: str, body: dict):
        if not roles or role_name not in roles:
            raise HTTPException(status_code=404, detail=f"Role '{role_name}' not found")
        rc = roles[role_name]
        if "system_prompt" in body:
            rc.system_prompt = body["system_prompt"]
        if "allowed_tools" in body:
            rc.allowed_tools = body["allowed_tools"]
        if "model" in body:
            rc.model = body["model"]
        return {"status": "ok", "role": role_name}

    # ------------------------------------------------------------------
    # Usage
    # ------------------------------------------------------------------

    @app.get("/api/usage")
    async def get_usage():
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        daily = await task_board._db.get_usage_summary(today_start)
        weekly = await task_board._db.get_usage_summary(week_start)
        return {
            "daily": daily,
            "weekly": weekly,
            "today": today_start,
            "week_start": week_start,
        }

    # ------------------------------------------------------------------
    # Board filters
    # ------------------------------------------------------------------

    @app.get("/api/board/filters")
    async def get_board_filters():
        groups = await task_board.get_groups()
        instances = await instance_manager.get_all_instances()
        role_names = list(set(i["role"] for i in instances)) if instances else []
        return {
            "groups": [{"id": g["id"], "title": g["title"]} for g in groups],
            "roles": role_names if role_names else (list(roles.keys()) if roles else []),
            "statuses": ["blocked", "pending", "in_progress", "completed", "failed", "rejected"],
            "priorities": ["critical", "high", "medium", "low"],
        }

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws_manager.connect(ws)
        try:
            while True:
                data = await ws.receive_text()
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)

    # ------------------------------------------------------------------
    # Chat endpoints (kept as-is)
    # ------------------------------------------------------------------

    if chat_manager:
        from ai_team.agents.roles import get_agent_config

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
                    msg = json.loads(data)
                    msg_type = msg.get("type")

                    if msg_type == "start_session":
                        try:
                            config = get_agent_config(agent_name)
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
                                await ws.send_text(json.dumps({
                                    "type": "chat_token",
                                    "agent": agent_name,
                                    "content": text,
                                }))

                            async def on_tool_use(tool, tool_input):
                                await ws.send_text(json.dumps({
                                    "type": "chat_tool_use",
                                    "agent": agent_name,
                                    "tool": tool,
                                    "input": tool_input,
                                }))

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
                            await ws.send_text(json.dumps({
                                "type": "chat_error",
                                "agent": agent_name,
                                "error": str(e),
                            }))

                    elif msg_type == "stop_session":
                        await chat_manager.stop_session(agent_name)
                        await ws.send_text(json.dumps({
                            "type": "session_stopped",
                            "agent": agent_name,
                        }))
                        break

            except WebSocketDisconnect:
                await chat_manager.stop_session(agent_name)

    # ------------------------------------------------------------------
    # Template rendering
    # ------------------------------------------------------------------

    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    return app
