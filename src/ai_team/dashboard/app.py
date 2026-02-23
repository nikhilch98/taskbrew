"""FastAPI dashboard backend with WebSocket support."""

import asyncio
import json
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from ai_team.orchestrator.event_bus import EventBus
from ai_team.orchestrator.team_manager import TeamManager
from ai_team.orchestrator.task_queue import TaskQueue
from ai_team.orchestrator.workflow import WorkflowEngine


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

    @app.get("/")
    async def index():
        return HTMLResponse(DASHBOARD_HTML)

    return app


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>AI Team Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }
        h1 { color: #58a6ff; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card h3 { color: #58a6ff; margin-bottom: 8px; }
        .status { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
        .status.idle { background: #238636; }
        .status.working { background: #d29922; }
        .status.error { background: #da3633; }
        #log { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 16px; max-height: 400px; overflow-y: auto; font-family: monospace; font-size: 13px; }
        .log-entry { padding: 4px 0; border-bottom: 1px solid #21262d; }
    </style>
</head>
<body>
    <h1>AI Team Dashboard</h1>
    <div class="grid" id="agents"></div>
    <h2 style="color:#58a6ff;margin-bottom:12px">Event Log</h2>
    <div id="log"></div>
    <script>
        const ws = new WebSocket(`ws://${location.host}/ws`);
        const log = document.getElementById('log');
        const agents = document.getElementById('agents');
        ws.onmessage = (e) => {
            const event = JSON.parse(e.data);
            const entry = document.createElement('div');
            entry.className = 'log-entry';
            entry.textContent = `[${new Date().toLocaleTimeString()}] ${event.type}: ${JSON.stringify(event)}`;
            log.prepend(entry);
        };
        async function refreshTeam() {
            const resp = await fetch('/api/team');
            const team = await resp.json();
            agents.innerHTML = '';
            for (const [name, status] of Object.entries(team)) {
                agents.innerHTML += `<div class="card"><h3>${name}</h3><span class="status ${status}">${status}</span></div>`;
            }
        }
        setInterval(refreshTeam, 3000);
        refreshTeam();
    </script>
</body>
</html>
"""
