# Plugins

Place Python plugin files in this directory to extend taskbrew.

## Plugin Structure

Each plugin is a Python file (or package with `__init__.py`) that exposes
a `register(registry)` function.  The registry is a `PluginRegistry` instance
that the plugin uses to declare its metadata and hook callbacks.

```python
# plugins/my_plugin.py
from taskbrew.plugin_system import PluginMetadata


def register(registry):
    """Called automatically when the orchestrator starts."""
    meta = PluginMetadata(
        name="my-plugin",
        version="1.0.0",
        description="What this plugin does",
    )
    plugin = registry.register_plugin(meta)

    # Register hook callbacks
    registry.register_hook("task.completed", on_task_completed, plugin_name=meta.name)


async def on_task_completed(event):
    """React to task completion."""
    task_id = event.get("task_id")
    print(f"Task {task_id} completed!")
```

## Available Hooks

Hooks are fired by the orchestrator at key lifecycle points.  Use
`registry.register_hook(hook_name, callback)` inside your `register()`
function.

| Hook | Payload | Description |
|------|---------|-------------|
| `task.created` | `{"task_id", "title", ...}` | A new task was created |
| `task.completed` | `{"task_id", ...}` | A task finished successfully |
| `task.failed` | `{"task_id", "error", ...}` | A task failed |
| `task.claimed` | `{"task_id", "instance_id"}` | An agent claimed a task |
| `task.recovered` | `{"task_id"}` | An orphaned task was recovered |
| `agent.text` | `{"instance_id", "text"}` | Agent produced text output |
| `agent.result` | `{"instance_id", "result"}` | Agent produced a result |
| `agent.status_changed` | `{"instance_id", "status"}` | Agent status changed |
| `tool.pre_use` | `{"tool", "args"}` | Before a tool is invoked |
| `tool.post_use` | `{"tool", "result"}` | After a tool returns |

Callbacks can be sync or async.  Async callbacks are awaited automatically.

## Registering API Routes

Plugins can expose additional FastAPI routes:

```python
from fastapi import APIRouter

def register(registry):
    meta = PluginMetadata(name="my-routes", version="0.1.0")
    plugin = registry.register_plugin(meta)

    router = APIRouter(prefix="/api/plugins/my-routes")

    @router.get("/status")
    async def status():
        return {"ok": True}

    plugin.routes.append(router)
```

Routes are collected via `registry.get_all_routes()` and can be included
by the dashboard app.

## Registering Custom Tools

Plugins can also register tools that agents can use:

```python
def register(registry):
    meta = PluginMetadata(name="my-tools", version="0.1.0")
    plugin = registry.register_plugin(meta)

    plugin.tools.append({
        "name": "my_custom_tool",
        "description": "Does something useful",
        "handler": my_tool_handler,
    })
```

Tools are collected via `registry.get_all_tools()`.

## Plugin Discovery

At startup the orchestrator scans this directory for:
- Python files (`*.py`, excluding files starting with `_`)
- Python packages (directories containing `__init__.py`)

Each discovered module must have a top-level `register(registry)` function.
Modules without this function are skipped with a warning.

## Managing Plugins at Runtime

The `PluginRegistry` supports enabling/disabling plugins without
unloading them:

```python
registry.disable_plugin("my-plugin")   # hooks still registered but plugin marked disabled
registry.enable_plugin("my-plugin")    # re-enable
registry.unregister_plugin("my-plugin")  # fully remove plugin and its hooks
```

Plugin metadata is available via `registry.get_plugin_info()`.
