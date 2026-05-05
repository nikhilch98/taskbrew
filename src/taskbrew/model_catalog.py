"""Provider-aware model catalog and role defaults."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PROVIDER_ORDER = ("claude", "gemini", "codex")

_COMMON_CODEX_REASONING = ["low", "medium", "high", "xhigh"]
_CLAUDE_OPUS_47_REASONING = ["low", "medium", "high", "xhigh", "max"]
_CLAUDE_46_REASONING = ["low", "medium", "high", "max"]


MODEL_CATALOG: dict[str, list[dict[str, Any]]] = {
    "claude": [
        {
            "id": "claude-opus-4-7",
            "label": "Flagship",
            "name": "Claude Opus 4.7",
            "reasoning_label": "Effort",
            "reasoning_efforts": _CLAUDE_OPUS_47_REASONING,
            "default_reasoning_effort": "xhigh",
        },
        {
            "id": "claude-opus-4-6",
            "label": "Previous Opus",
            "name": "Claude Opus 4.6",
            "reasoning_label": "Effort",
            "reasoning_efforts": _CLAUDE_46_REASONING,
            "default_reasoning_effort": "high",
        },
        {
            "id": "claude-sonnet-4-6",
            "label": "Balanced",
            "name": "Claude Sonnet 4.6",
            "reasoning_label": "Effort",
            "reasoning_efforts": _CLAUDE_46_REASONING,
            "default_reasoning_effort": "high",
        },
        {
            "id": "claude-haiku-4-5",
            "label": "Fast",
            "name": "Claude Haiku 4.5 Latest",
        },
        {
            "id": "claude-haiku-4-5-20251001",
            "label": "Pinned Fast",
            "name": "Claude Haiku 4.5",
        },
    ],
    "gemini": [
        {
            "id": "gemini-3-pro-preview",
            "label": "Flagship",
            "name": "Gemini 3 Pro Preview",
            "reasoning_label": "Thinking",
            "reasoning_efforts": ["low", "high"],
            "default_reasoning_effort": "high",
            "thinking_config_type": "level",
        },
        {
            "id": "gemini-3.1-pro-preview",
            "label": "Preview",
            "name": "Gemini 3.1 Pro Preview",
            "reasoning_label": "Thinking",
            "reasoning_efforts": ["low", "high"],
            "default_reasoning_effort": "high",
            "thinking_config_type": "level",
        },
        {
            "id": "gemini-3-flash-preview",
            "label": "Balanced",
            "name": "Gemini 3 Flash Preview",
            "reasoning_label": "Thinking",
            "reasoning_efforts": ["minimal", "low", "medium", "high"],
            "default_reasoning_effort": "medium",
            "thinking_config_type": "level",
        },
        {
            "id": "gemini-2.5-pro",
            "label": "Previous Pro",
            "name": "Gemini 2.5 Pro",
            "reasoning_label": "Thinking",
            "reasoning_efforts": ["dynamic", "low", "medium", "high"],
            "default_reasoning_effort": "dynamic",
            "thinking_config_type": "budget",
        },
        {
            "id": "gemini-2.5-flash",
            "label": "Previous Flash",
            "name": "Gemini 2.5 Flash",
            "reasoning_label": "Thinking",
            "reasoning_efforts": ["off", "dynamic", "low", "medium", "high"],
            "default_reasoning_effort": "dynamic",
            "thinking_config_type": "budget",
        },
    ],
    "codex": [
        {
            "id": "gpt-5.5",
            "label": "Flagship",
            "name": "GPT-5.5",
            "reasoning_label": "Reasoning",
            "reasoning_efforts": _COMMON_CODEX_REASONING,
            "default_reasoning_effort": "xhigh",
        },
        {
            "id": "gpt-5.4",
            "label": "Balanced",
            "name": "GPT-5.4",
            "reasoning_label": "Reasoning",
            "reasoning_efforts": _COMMON_CODEX_REASONING,
            "default_reasoning_effort": "medium",
        },
        {
            "id": "gpt-5.4-mini",
            "label": "Fast",
            "name": "GPT-5.4 Mini",
            "reasoning_label": "Reasoning",
            "reasoning_efforts": _COMMON_CODEX_REASONING,
            "default_reasoning_effort": "medium",
        },
        {
            "id": "gpt-5.3-codex",
            "label": "Codex",
            "name": "GPT-5.3 Codex",
            "reasoning_label": "Reasoning",
            "reasoning_efforts": _COMMON_CODEX_REASONING,
            "default_reasoning_effort": "medium",
        },
        {
            "id": "gpt-5.3-codex-spark",
            "label": "Codex Fast",
            "name": "GPT-5.3 Codex Spark",
            "reasoning_label": "Reasoning",
            "reasoning_efforts": _COMMON_CODEX_REASONING,
            "default_reasoning_effort": "high",
        },
        {
            "id": "gpt-5.2",
            "label": "Previous",
            "name": "GPT-5.2",
            "reasoning_label": "Reasoning",
            "reasoning_efforts": _COMMON_CODEX_REASONING,
            "default_reasoning_effort": "medium",
        },
    ],
}


_ROLE_MODEL_TIER: dict[str, str] = {
    "pm": "flagship",
    "architect": "flagship",
    "coder": "balanced",
    "verifier": "balanced",
}

_PROVIDER_ROLE_DEFAULTS: dict[str, dict[str, str]] = {
    "claude": {
        "flagship": "claude-opus-4-7",
        "balanced": "claude-sonnet-4-6",
        "fast": "claude-haiku-4-5",
    },
    "gemini": {
        "flagship": "gemini-3-pro-preview",
        "balanced": "gemini-3-flash-preview",
        "fast": "gemini-3-flash-preview",
    },
    "codex": {
        "flagship": "gpt-5.5",
        "balanced": "gpt-5.5",
        "fast": "gpt-5.4-mini",
    },
}


def _provider_defaults(provider: str | None) -> dict[str, str]:
    """Return tier defaults for a supported provider."""
    provider_name = provider if provider in _PROVIDER_ROLE_DEFAULTS else "claude"
    return _PROVIDER_ROLE_DEFAULTS[provider_name]


def available_models(provider: str = "") -> list[dict[str, Any]]:
    """Return all model entries, optionally filtered to one provider."""
    providers = [provider] if provider else list(PROVIDER_ORDER)
    models: list[dict[str, Any]] = []
    for provider_name in providers:
        for model in MODEL_CATALOG.get(provider_name, []):
            models.append({"provider": provider_name, **deepcopy(model)})
    return models


def model_entry(model_id: str | None) -> dict[str, Any] | None:
    """Return catalog metadata for *model_id*, if known."""
    if not model_id:
        return None
    for models in MODEL_CATALOG.values():
        for model in models:
            if model["id"] == model_id:
                return deepcopy(model)
    return None


def model_for_role(role_name: str, provider: str) -> str:
    """Return the default model ID for a role/provider pair."""
    provider_defaults = _provider_defaults(provider)
    tier = _ROLE_MODEL_TIER.get(role_name, "balanced")
    return provider_defaults.get(tier) or provider_defaults["balanced"]


def model_for_system_agent(provider: str) -> str:
    """Return the default model ID for the per-project system agent."""
    return _provider_defaults(provider)["balanced"]


def reasoning_efforts_for_model(model_id: str | None) -> list[str]:
    """Return supported reasoning/thinking efforts for a model."""
    entry = model_entry(model_id)
    if not entry:
        return []
    return list(entry.get("reasoning_efforts") or [])


def default_reasoning_effort_for_model(model_id: str | None) -> str | None:
    """Return the default reasoning/thinking effort for a model."""
    entry = model_entry(model_id)
    if not entry:
        return None
    effort = entry.get("default_reasoning_effort")
    return str(effort) if effort else None


def normalize_reasoning_effort(
    model_id: str | None,
    effort: str | None,
) -> str | None:
    """Return *effort* if the catalog supports it, otherwise a safe default."""
    if not effort:
        return default_reasoning_effort_for_model(model_id)
    cleaned = effort.strip().lower()
    allowed = reasoning_efforts_for_model(model_id)
    if not allowed or cleaned in allowed:
        return cleaned
    return default_reasoning_effort_for_model(model_id)


def role_model_setting(
    role_name: str,
    provider: str,
    role_model_settings: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve model and reasoning settings for a scaffolded role."""
    raw = (role_model_settings or {}).get(role_name) or {}
    if isinstance(raw, str):
        raw = {"model": raw}
    model = str(raw.get("model") or model_for_role(role_name, provider))
    effort = normalize_reasoning_effort(model, raw.get("reasoning_effort"))
    result = {"model": model}
    if effort:
        result["reasoning_effort"] = effort
    return result


def system_agent_setting(
    cli_provider: str,
    system_agent_settings: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve provider, model, and reasoning settings for the system agent."""
    raw = system_agent_settings or {}
    if isinstance(raw, str):
        raw = {"provider": raw}
    provider = str(raw.get("provider") or cli_provider or "codex").strip().lower()
    if provider not in MODEL_CATALOG:
        provider = "codex"
    model = str(raw.get("model") or model_for_system_agent(provider))
    effort = normalize_reasoning_effort(model, raw.get("reasoning_effort"))
    result = {"provider": provider, "model": model}
    if effort:
        result["reasoning_effort"] = effort
    return result
