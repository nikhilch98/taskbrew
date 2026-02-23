"""Tests for the ProviderRegistry."""
from taskbrew.agents.provider import ProviderRegistry


def test_registry_detect_claude():
    registry = ProviderRegistry()
    registry.register_builtins()
    assert registry.detect("claude-opus-4-6") == "claude"
    assert registry.detect("claude-sonnet-4-6") == "claude"


def test_registry_detect_gemini():
    registry = ProviderRegistry()
    registry.register_builtins()
    assert registry.detect("gemini-3.1-pro-preview") == "gemini"


def test_registry_detect_default():
    registry = ProviderRegistry()
    registry.register_builtins()
    assert registry.detect("unknown-model") == "claude"


def test_registry_load_yaml_provider(tmp_path):
    import yaml
    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    (providers_dir / "test.yaml").write_text(yaml.dump({
        "name": "test",
        "display_name": "Test Provider",
        "binary": "test-cli",
        "detect_models": ["test-*"],
        "command_template": {
            "prompt_flag": "-p",
            "output_format_flag": "--output-format",
            "output_format_value": "stream-json",
            "model_flag": "-m",
            "auto_approve_flag": "-y",
        },
        "output_parser": "stream-json",
        "system_prompt_mode": "xml-inject",
        "models": [{"id": "test-latest", "tier": "flagship"}],
    }))
    registry = ProviderRegistry()
    registry.register_builtins()
    loaded = registry.load_yaml_providers(providers_dir)
    assert "test" in loaded
    assert registry.detect("test-latest") == "test"


def test_registry_load_empty_dir(tmp_path):
    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    registry = ProviderRegistry()
    loaded = registry.load_yaml_providers(providers_dir)
    assert loaded == []


def test_registry_load_nonexistent_dir(tmp_path):
    registry = ProviderRegistry()
    loaded = registry.load_yaml_providers(tmp_path / "nonexistent")
    assert loaded == []


def test_malformed_yaml_skipped(tmp_path):
    """Malformed provider YAML should be skipped with a warning."""
    providers_dir = tmp_path / "providers"
    providers_dir.mkdir()
    (providers_dir / "broken.yaml").write_text("{{{{invalid yaml")
    (providers_dir / "valid.yaml").write_text(
        'name: valid\ndetect_models: ["valid-*"]\n'
    )
    registry = ProviderRegistry()
    registry.register_builtins()
    loaded = registry.load_yaml_providers(providers_dir)
    assert "valid" in loaded
    assert "broken" not in loaded
