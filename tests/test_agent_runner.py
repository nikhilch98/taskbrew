"""Tests for provider result handling in AgentRunner."""

from __future__ import annotations

import pytest

from taskbrew.agents.base import AgentRunner
from taskbrew.agents.codex_cli import ResultMessage
from taskbrew.config import AgentConfig


@pytest.mark.asyncio
async def test_agent_runner_raises_on_provider_usage_limit(monkeypatch):
    async def fake_sdk_query(*args, **kwargs):
        yield ResultMessage(
            result=(
                "You've hit your usage limit. Visit "
                "https://chatgpt.com/codex/settings/usage to purchase more credits."
            ),
        )

    monkeypatch.setattr("taskbrew.agents.base.sdk_query", fake_sdk_query)

    runner = AgentRunner(
        AgentConfig(
            name="coder-1",
            role="coder",
            system_prompt="You write code.",
            model="gpt-5.5",
            cli_provider="codex",
        )
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await runner.run("Do the task.")

