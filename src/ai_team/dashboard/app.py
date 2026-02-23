"""FastAPI dashboard backend with WebSocket support."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from ai_team.agents.base import AgentStatus
from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.team_manager import TeamManager
from ai_team.orchestrator.task_queue import TaskQueue
from ai_team.orchestrator.workflow import WorkflowEngine

if TYPE_CHECKING:
    from ai_team.dashboard.chat_manager import ChatManager


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
    team_manager: TeamManager,
    task_queue: TaskQueue,
    workflow_engine: WorkflowEngine,
    chat_manager: ChatManager | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Team Dashboard")
    ws_manager = ConnectionManager()

    async def broadcast_event(event: dict):
        await ws_manager.broadcast(event)

    event_bus.subscribe("*", broadcast_event)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/team")
    async def get_team():
        return {name: str(status) for name, status in team_manager.get_team_status().items()}

    @app.get("/api/tasks")
    async def get_tasks():
        return await task_queue.get_pending_tasks()

    @app.get("/api/pipelines")
    async def get_pipelines():
        return [
            {"name": p.name, "description": p.description, "steps": len(p.steps)}
            for p in workflow_engine.pipelines.values()
        ]

    @app.post("/api/pipelines/{pipeline_name}/run")
    async def start_pipeline(pipeline_name: str, goal: dict):
        import uuid
        run_id = str(uuid.uuid4())[:8]
        run = workflow_engine.start_run(pipeline_name, run_id, initial_context=goal)
        step = workflow_engine.get_current_step(run_id)
        if step:
            task_id = await task_queue.create_task(
                pipeline_id=run_id, task_type=step.action, input_context=json.dumps(goal)
            )
            await event_bus.emit(
                "pipeline_started",
                {"run_id": run_id, "pipeline": pipeline_name, "first_task": task_id},
            )
        return {"run_id": run_id, "status": "started"}

    @app.post("/api/runs/{run_id}/approve")
    async def approve_run(run_id: str):
        run = workflow_engine.active_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != "awaiting_approval":
            raise HTTPException(status_code=400, detail="Run not awaiting approval")
        workflow_engine.approve_checkpoint(run_id)
        await event_bus.emit("checkpoint_approved", {"run_id": run_id})
        return {"status": "approved", "run_id": run_id}

    @app.post("/api/runs/{run_id}/reject")
    async def reject_run(run_id: str, body: dict = {}):
        run = workflow_engine.active_runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != "awaiting_approval":
            raise HTTPException(status_code=400, detail="Run not awaiting approval")
        reason = body.get("reason", "")
        workflow_engine.reject_checkpoint(run_id, reason=reason)
        await event_bus.emit("checkpoint_rejected", {"run_id": run_id, "reason": reason})
        return {"status": "rejected", "run_id": run_id}

    @app.get("/api/tasks/board")
    async def get_task_board():
        """Return tasks grouped by status for Kanban board."""
        all_tasks = await task_queue.get_pending_tasks()
        board = {
            "pending": [],
            "assigned": [],
            "in_progress": [],
            "review": [],
            "completed": [],
            "failed": [],
        }
        for task in all_tasks:
            status = task.get("status", "pending")
            if status in board:
                board[status].append(task)
            else:
                board["pending"].append(task)
        return board

    @app.get("/api/runs")
    async def get_runs():
        """Return all active pipeline runs."""
        return [
            {
                "run_id": run.run_id,
                "pipeline": run.pipeline_name,
                "current_step": run.current_step,
                "status": run.status,
            }
            for run in workflow_engine.active_runs.values()
        ]

    @app.post("/api/agents/{agent_name}/pause")
    async def pause_agent(agent_name: str):
        agent = team_manager.get_agent(agent_name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
        agent.status = AgentStatus.BLOCKED
        await event_bus.emit("agent_paused", {"agent": agent_name})
        return {"agent": agent_name, "status": "blocked"}

    @app.post("/api/agents/{agent_name}/resume")
    async def resume_agent(agent_name: str):
        agent = team_manager.get_agent(agent_name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
        agent.status = AgentStatus.IDLE
        await event_bus.emit("agent_resumed", {"agent": agent_name})
        return {"agent": agent_name, "status": "idle"}

    @app.post("/api/agents/{agent_name}/kill")
    async def kill_agent(agent_name: str):
        agent = team_manager.get_agent(agent_name)
        if not agent:
            raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")
        team_manager.stop_agent(agent_name)
        await event_bus.emit("agent_killed", {"agent": agent_name})
        return {"agent": agent_name, "status": "stopped"}

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

    templates_dir = Path(__file__).parent / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    @app.get("/")
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    return app
