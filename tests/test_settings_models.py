"""Tests for provider-aware settings model catalog."""

import pytest


@pytest.mark.asyncio
async def test_settings_models_returns_all_provider_families():
    from taskbrew.dashboard.routers.system import get_available_models

    models = await get_available_models()
    providers = {m["provider"] for m in models}
    ids = {m["id"] for m in models}

    assert {"claude", "gemini", "codex"} <= providers
    assert "claude-sonnet-4-6" in ids
    assert "gemini-3-flash-preview" in ids
    assert "gpt-5.5" in ids
    assert "gpt-5.3-codex" in ids
    assert "gpt-5.3-codex-spark" in ids


@pytest.mark.asyncio
async def test_settings_models_can_filter_by_provider():
    from taskbrew.dashboard.routers.system import get_available_models

    models = await get_available_models(provider="gemini")

    assert models
    assert {m["provider"] for m in models} == {"gemini"}
