"""Tests for plugin system integration."""

from pathlib import Path

from taskbrew.plugin_system import PluginRegistry


def test_plugin_registry_loads_from_directory(tmp_path):
    """Plugin registry loads plugins from a directory."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "example.py").write_text(
        "from taskbrew.plugin_system import PluginMetadata\n"
        "\n"
        "def register(registry):\n"
        '    meta = PluginMetadata(name="example", version="1.0.0")\n'
        "    registry.register_plugin(meta)\n"
    )
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert loaded == ["example"]
    assert "example" in registry.plugins


def test_plugin_registry_loads_multiple_plugins(tmp_path):
    """Plugin registry loads multiple plugins sorted by filename."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    for name in ("alpha", "beta"):
        (plugins_dir / f"{name}.py").write_text(
            "from taskbrew.plugin_system import PluginMetadata\n"
            "\n"
            "def register(registry):\n"
            f'    meta = PluginMetadata(name="{name}", version="0.1.0")\n'
            "    registry.register_plugin(meta)\n"
        )
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert loaded == ["alpha", "beta"]
    assert len(registry.plugins) == 2


def test_plugin_registry_empty_dir(tmp_path):
    """Plugin registry handles empty directory gracefully."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert loaded == []
    assert len(registry.plugins) == 0


def test_plugin_registry_no_dir():
    """Plugin registry handles missing directory gracefully."""
    registry = PluginRegistry()
    loaded = registry.load_plugins(Path("/nonexistent/path"))
    assert loaded == []


def test_plugin_registry_skips_files_without_register(tmp_path):
    """Plugin registry skips files that lack a register() function."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "no_register.py").write_text(
        '# This plugin has no register() function\n'
        'metadata = {"name": "bad"}\n'
    )
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert loaded == []
    assert len(registry.plugins) == 0


def test_plugin_registry_skips_underscore_files(tmp_path):
    """Plugin registry ignores files starting with underscore."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "_internal.py").write_text(
        "from taskbrew.plugin_system import PluginMetadata\n"
        "\n"
        "def register(registry):\n"
        '    registry.register_plugin(PluginMetadata(name="_internal"))\n'
    )
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert loaded == []


def test_plugin_with_hooks(tmp_path):
    """Plugin can register hooks during load."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "hooky.py").write_text(
        "from taskbrew.plugin_system import PluginMetadata\n"
        "\n"
        "def register(registry):\n"
        '    meta = PluginMetadata(name="hooky", version="0.1.0")\n'
        "    registry.register_plugin(meta)\n"
        '    registry.register_hook("task.completed", _on_complete, plugin_name="hooky")\n'
        "\n"
        "def _on_complete(data):\n"
        "    pass\n"
    )
    registry = PluginRegistry()
    loaded = registry.load_plugins(plugins_dir)
    assert loaded == ["hooky"]
    # Verify the hook was registered
    assert "task.completed" in registry._hooks
    assert len(registry._hooks["task.completed"]) == 1


def test_plugin_get_info(tmp_path):
    """Plugin info is available after loading."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "info_test.py").write_text(
        "from taskbrew.plugin_system import PluginMetadata\n"
        "\n"
        "def register(registry):\n"
        '    meta = PluginMetadata(name="info-test", version="2.0.0", '
        'description="A test plugin", author="tester")\n'
        "    registry.register_plugin(meta)\n"
    )
    registry = PluginRegistry()
    registry.load_plugins(plugins_dir)
    info = registry.get_plugin_info()
    assert len(info) == 1
    assert info[0]["name"] == "info-test"
    assert info[0]["version"] == "2.0.0"
    assert info[0]["description"] == "A test plugin"
    assert info[0]["author"] == "tester"
    assert info[0]["enabled"] is True
