"""Tests for the plugin/extension system."""

import pytest
from pathlib import Path

from taskbrew.plugin_system import PluginRegistry, PluginMetadata


@pytest.fixture
def registry():
    return PluginRegistry()


class TestPluginRegistration:
    def test_register_plugin(self, registry):
        meta = PluginMetadata(name="test-plugin", version="1.0.0", description="Test")
        plugin = registry.register_plugin(meta)
        assert plugin.metadata.name == "test-plugin"
        assert "test-plugin" in registry.plugins

    def test_register_duplicate_raises(self, registry):
        meta = PluginMetadata(name="test-plugin")
        registry.register_plugin(meta)
        with pytest.raises(ValueError, match="already registered"):
            registry.register_plugin(meta)

    def test_unregister_plugin(self, registry):
        meta = PluginMetadata(name="test-plugin")
        registry.register_plugin(meta)
        registry.unregister_plugin("test-plugin")
        assert "test-plugin" not in registry.plugins

    def test_unregister_nonexistent(self, registry):
        # Should not raise
        registry.unregister_plugin("nonexistent")


class TestHooks:
    async def test_register_and_fire_hook(self, registry):
        results = []

        async def on_task_created(data):
            results.append(data)
            return "handled"

        registry.register_hook("task.created", on_task_created)
        ret = await registry.fire_hook("task.created", {"id": "T1"})
        assert results == [{"id": "T1"}]
        assert ret == ["handled"]

    async def test_fire_unregistered_hook(self, registry):
        ret = await registry.fire_hook("nonexistent.hook")
        assert ret == []

    async def test_multiple_hooks(self, registry):
        calls = []

        async def hook1(data):
            calls.append("hook1")

        async def hook2(data):
            calls.append("hook2")

        registry.register_hook("test", hook1)
        registry.register_hook("test", hook2)
        await registry.fire_hook("test", None)
        assert calls == ["hook1", "hook2"]

    async def test_sync_hook(self, registry):
        results = []

        def sync_hook(data):
            results.append(data)

        registry.register_hook("sync", sync_hook)
        await registry.fire_hook("sync", "hello")
        assert results == ["hello"]

    async def test_hook_error_handling(self, registry):
        async def bad_hook(data):
            raise RuntimeError("oops")

        async def good_hook(data):
            return "ok"

        registry.register_hook("test", bad_hook)
        registry.register_hook("test", good_hook)
        # Should not raise, bad hook is caught
        ret = await registry.fire_hook("test", None)
        assert "ok" in ret

    async def test_unregister_removes_hooks(self, registry):
        calls = []

        async def plugin_hook(data):
            calls.append("called")

        meta = PluginMetadata(name="test-plugin")
        registry.register_plugin(meta)
        registry.register_hook("test", plugin_hook, plugin_name="test-plugin")
        registry.unregister_plugin("test-plugin")
        await registry.fire_hook("test", None)
        assert calls == []


class TestPluginInfo:
    def test_get_plugin_info(self, registry):
        meta = PluginMetadata(name="my-plugin", version="2.0", description="My plugin", author="Test")
        registry.register_plugin(meta)
        info = registry.get_plugin_info()
        assert len(info) == 1
        assert info[0]["name"] == "my-plugin"
        assert info[0]["version"] == "2.0"
        assert info[0]["enabled"] is True

    def test_enable_disable(self, registry):
        meta = PluginMetadata(name="toggle")
        registry.register_plugin(meta)
        assert registry.disable_plugin("toggle") is True
        assert registry.plugins["toggle"].metadata.enabled is False
        assert registry.enable_plugin("toggle") is True
        assert registry.plugins["toggle"].metadata.enabled is True

    def test_enable_nonexistent(self, registry):
        assert registry.enable_plugin("nope") is False
        assert registry.disable_plugin("nope") is False


class TestLoadPlugins:
    def test_load_from_empty_dir(self, registry, tmp_path):
        loaded = registry.load_plugins(tmp_path)
        assert loaded == []

    def test_load_from_nonexistent_dir(self, registry):
        loaded = registry.load_plugins(Path("/nonexistent/path"))
        assert loaded == []

    def test_load_plugin_file(self, registry, tmp_path):
        # Create a simple plugin
        plugin_file = tmp_path / "hello_plugin.py"
        plugin_file.write_text(
            "from taskbrew.plugin_system import PluginMetadata\n"
            "def register(registry):\n"
            "    meta = PluginMetadata(name='hello', version='1.0')\n"
            "    registry.register_plugin(meta)\n"
        )
        loaded = registry.load_plugins(tmp_path)
        assert "hello_plugin" in loaded
        assert "hello" in registry.plugins

    def test_skip_underscore_files(self, registry, tmp_path):
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "_private.py").write_text("def register(r): pass")
        loaded = registry.load_plugins(tmp_path)
        assert loaded == []

    def test_skip_no_register(self, registry, tmp_path):
        (tmp_path / "bad_plugin.py").write_text("x = 1\n")
        loaded = registry.load_plugins(tmp_path)
        assert loaded == []


class TestRoutes:
    def test_get_all_routes(self, registry):
        meta = PluginMetadata(name="routes-plugin")
        plugin = registry.register_plugin(meta)
        plugin.routes.append({"path": "/api/custom", "method": "GET"})
        routes = registry.get_all_routes()
        assert len(routes) == 1

    def test_disabled_plugin_routes_excluded(self, registry):
        meta = PluginMetadata(name="routes-plugin", enabled=False)
        plugin = registry.register_plugin(meta)
        plugin.routes.append({"path": "/api/custom"})
        routes = registry.get_all_routes()
        assert len(routes) == 0


class TestTools:
    def test_get_all_tools(self, registry):
        meta = PluginMetadata(name="tools-plugin")
        plugin = registry.register_plugin(meta)
        plugin.tools.append({"name": "custom_tool"})
        tools = registry.get_all_tools()
        assert len(tools) == 1
