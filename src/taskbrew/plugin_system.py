"""Plugin/extension system for TaskBrew.

Provides a simple hook-based plugin architecture that allows extensions to:
- Listen to events (task lifecycle, agent actions, etc.)
- Add custom API endpoints
- Register custom tools
- Modify agent behavior via middleware

Plugins are Python modules in the `plugins/` directory with a `register()` function.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PluginMetadata:
    """Metadata for a registered plugin."""

    name: str
    version: str = "0.0.1"
    description: str = ""
    author: str = ""
    enabled: bool = True


@dataclass
class Plugin:
    """A loaded plugin with its metadata and hooks."""

    metadata: PluginMetadata
    hooks: dict[str, list[Callable]] = field(default_factory=dict)
    routes: list[Any] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)


class PluginRegistry:
    """Central registry for managing plugins and their hooks.

    Usage::

        registry = PluginRegistry()
        registry.load_plugins(Path("plugins/"))

        # Fire a hook
        await registry.fire_hook("task.created", task_data)

        # Get all registered routes
        routes = registry.get_all_routes()
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._hooks: dict[str, list[Callable]] = {}

    @property
    def plugins(self) -> dict[str, Plugin]:
        """All registered plugins."""
        return dict(self._plugins)

    def register_plugin(self, metadata: PluginMetadata) -> Plugin:
        """Register a new plugin and return its Plugin handle."""
        if metadata.name in self._plugins:
            raise ValueError(f"Plugin already registered: {metadata.name}")
        plugin = Plugin(metadata=metadata)
        self._plugins[metadata.name] = plugin
        logger.info("Registered plugin: %s v%s", metadata.name, metadata.version)
        return plugin

    def unregister_plugin(self, name: str) -> None:
        """Unregister a plugin and remove all its hooks."""
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            return
        # Remove hooks
        for hook_name, callbacks in list(self._hooks.items()):
            self._hooks[hook_name] = [
                cb for cb in callbacks if not getattr(cb, "_plugin_name", None) == name
            ]
        logger.info("Unregistered plugin: %s", name)

    def register_hook(self, hook_name: str, callback: Callable, plugin_name: str | None = None) -> None:
        """Register a callback for a named hook.

        Parameters
        ----------
        hook_name:
            Event/hook name (e.g., "task.created", "task.completed", "agent.started").
        callback:
            Async or sync callable to invoke when the hook fires.
        plugin_name:
            Optional plugin name to associate with this hook.
        """
        if hook_name not in self._hooks:
            self._hooks[hook_name] = []
        if plugin_name:
            callback._plugin_name = plugin_name  # type: ignore[attr-defined]
        self._hooks[hook_name].append(callback)

    async def fire_hook(self, hook_name: str, data: Any = None) -> list[Any]:
        """Fire all callbacks registered for a hook.

        Returns a list of results from each callback.
        """
        callbacks = self._hooks.get(hook_name, [])
        results = []
        for cb in callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    result = await cb(data)
                else:
                    result = cb(data)
                results.append(result)
            except Exception as exc:
                logger.error("Plugin hook %s error: %s", hook_name, exc)
        return results

    def get_all_routes(self) -> list[Any]:
        """Get all API routes registered by plugins."""
        routes = []
        for plugin in self._plugins.values():
            if plugin.metadata.enabled:
                routes.extend(plugin.routes)
        return routes

    def get_all_tools(self) -> list[Any]:
        """Get all tools registered by plugins."""
        tools = []
        for plugin in self._plugins.values():
            if plugin.metadata.enabled:
                tools.extend(plugin.tools)
        return tools

    def load_plugins(self, plugins_dir: Path) -> list[str]:
        """Load all plugins from a directory.

        Each plugin should be a Python file or package with a `register(registry)` function.

        Returns list of loaded plugin names.
        """
        loaded = []
        if not plugins_dir.is_dir():
            return loaded

        for path in sorted(plugins_dir.iterdir()):
            if path.suffix == ".py" and not path.name.startswith("_"):
                name = path.stem
            elif path.is_dir() and (path / "__init__.py").exists():
                name = path.name
            else:
                continue

            try:
                spec = importlib.util.spec_from_file_location(
                    f"taskbrew_plugin_{name}",
                    str(path) if path.suffix == ".py" else str(path / "__init__.py"),
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    if hasattr(module, "register"):
                        module.register(self)
                        loaded.append(name)
                        logger.info("Loaded plugin: %s", name)
                    else:
                        logger.warning("Plugin %s has no register() function", name)
            except Exception as exc:
                logger.error("Failed to load plugin %s: %s", name, exc)

        return loaded

    def get_plugin_info(self) -> list[dict]:
        """Get metadata for all registered plugins."""
        return [
            {
                "name": p.metadata.name,
                "version": p.metadata.version,
                "description": p.metadata.description,
                "author": p.metadata.author,
                "enabled": p.metadata.enabled,
                "hooks": list(p.hooks.keys()),
                "routes_count": len(p.routes),
                "tools_count": len(p.tools),
            }
            for p in self._plugins.values()
        ]

    def enable_plugin(self, name: str) -> bool:
        """Enable a plugin."""
        if name not in self._plugins:
            return False
        self._plugins[name].metadata.enabled = True
        return True

    def disable_plugin(self, name: str) -> bool:
        """Disable a plugin without unregistering it."""
        if name not in self._plugins:
            return False
        self._plugins[name].metadata.enabled = False
        return True
