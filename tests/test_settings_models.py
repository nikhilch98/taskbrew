"""Tests for provider-aware settings model catalog."""

import pytest


@pytest.mark.asyncio
async def test_settings_models_returns_all_provider_families():
    from taskbrew.dashboard.routers.system import get_available_models

    models = await get_available_models()
    providers = {m["provider"] for m in models}
    ids = {m["id"] for m in models}

    assert {"claude", "codex"} <= providers
    assert "claude-opus-4-7" in ids
    assert "claude-sonnet-4-6" in ids
    assert "gpt-5.5" in ids
    assert "gpt-5.4" in ids
    assert "gpt-5.3-codex" in ids
    assert "gpt-5.3-codex-spark" in ids

    by_id = {m["id"]: m for m in models}
    assert by_id["claude-opus-4-7"]["default_reasoning_effort"] == "xhigh"
    assert "xhigh" in by_id["claude-opus-4-7"]["reasoning_efforts"]
    assert by_id["claude-sonnet-4-6"]["reasoning_efforts"] == [
        "low", "medium", "high", "max",
    ]
    assert "reasoning_efforts" not in by_id["claude-haiku-4-5"]
    assert by_id["gpt-5.5"]["default_reasoning_effort"] == "xhigh"
    assert by_id["gpt-5.4"]["reasoning_efforts"] == ["low", "medium", "high", "xhigh"]



