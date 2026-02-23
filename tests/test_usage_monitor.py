"""Tests for the Claude Code usage monitor endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from taskbrew.dashboard.app import create_app

MOCK_PROFILE = {
    "account": {"has_claude_max": True, "has_claude_pro": False},
    "organization": {
        "rate_limit_tier": "default_claude_max_20x",
        "has_extra_usage_enabled": True,
        "subscription_status": "active",
    },
}

MOCK_PLAN_LIMITS = {
    "limits": [
        {"label": "Current session", "pct_used": 7, "resets": "1:30pm (Asia/Calcutta)"},
        {"label": "Current week (all models)", "pct_used": 1, "resets": "Mar 6 at 8:30am (Asia/Calcutta)"},
        {"label": "Current week (Sonnet only)", "pct_used": 0},
        {"label": "Extra usage", "pct_used": 100, "resets": "Mar 1 (Asia/Calcutta)"},
    ],
    "extra_usage_spent": 68.38,
    "extra_usage_limit": 50.00,
}


@pytest.fixture
def sample_stats():
    return {
        "version": 2,
        "lastComputedDate": "2026-02-17",
        "dailyActivity": [
            {"date": "2026-02-24", "messageCount": 100, "sessionCount": 3, "toolCallCount": 25},
            {"date": "2026-02-25", "messageCount": 200, "sessionCount": 5, "toolCallCount": 50},
        ],
        "dailyModelTokens": [
            {
                "date": "2026-02-24",
                "tokensByModel": {"claude-opus-4-6": 50000, "claude-sonnet-4-6": 20000},
            },
            {
                "date": "2026-02-25",
                "tokensByModel": {"claude-opus-4-6": 80000},
            },
        ],
        "modelUsage": {
            "claude-opus-4-6": {
                "inputTokens": 1000,
                "outputTokens": 2000,
                "cacheReadInputTokens": 500000,
                "cacheCreationInputTokens": 10000,
            },
        },
        "totalSessions": 42,
        "totalMessages": 5000,
        "firstSessionDate": "2026-01-07T12:00:00.000Z",
        "hourCounts": {"9": 5, "10": 8},
    }


@pytest.fixture
def sample_session_jsonl(tmp_path):
    """Create a fake JSONL session file."""
    proj_dir = tmp_path / "projects" / "test-project"
    proj_dir.mkdir(parents=True)
    session = proj_dir / "abc-123.jsonl"
    lines = [
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-02-26T10:00:00.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-6",
                "content": [{"type": "text", "text": "hello"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 500,
                    "cache_read_input_tokens": 5000,
                    "cache_creation_input_tokens": 200,
                },
                "type": "message",
                "id": "msg_1",
                "stop_reason": "end_turn",
                "stop_sequence": None,
            },
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-02-26T10:05:00.000Z",
            "message": {
                "role": "assistant",
                "model": "claude-opus-4-6",
                "content": [{"type": "text", "text": "world"}],
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 800,
                    "cache_read_input_tokens": 6000,
                    "cache_creation_input_tokens": 300,
                },
                "type": "message",
                "id": "msg_2",
                "stop_reason": "end_turn",
                "stop_sequence": None,
            },
        }),
    ]
    session.write_text("\n".join(lines))
    return tmp_path


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_usage_summary_with_stats(client, sample_stats, tmp_path):
    stats_file = tmp_path / "stats-cache.json"
    stats_file.write_text(json.dumps(sample_stats))

    with patch("taskbrew.dashboard.routers.usage.STATS_CACHE", stats_file), \
         patch("taskbrew.dashboard.routers.usage.PROJECTS_DIR", tmp_path / "no-projects"), \
         patch("taskbrew.dashboard.routers.usage._fetch_profile", new_callable=AsyncMock, return_value=MOCK_PROFILE), \
         patch("taskbrew.dashboard.routers.usage._fetch_usage_via_cli", new_callable=AsyncMock, return_value=MOCK_PLAN_LIMITS):
        resp = await client.get("/api/usage/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert "week" in data
    assert data["stats_last_computed"] == "2026-02-17"
    assert data["plan"]["plan"] == "Claude Max"
    assert data["plan"]["extra_usage"] is True
    assert "hour_window" in data
    assert data["plan_limits"] is not None
    assert len(data["plan_limits"]["limits"]) == 4
    assert data["plan_limits"]["limits"][0]["pct_used"] == 7


@pytest.mark.asyncio
async def test_usage_summary_missing_file(client, tmp_path):
    missing = tmp_path / "nonexistent.json"
    with patch("taskbrew.dashboard.routers.usage.STATS_CACHE", missing), \
         patch("taskbrew.dashboard.routers.usage.PROJECTS_DIR", tmp_path / "no-projects"), \
         patch("taskbrew.dashboard.routers.usage._fetch_profile", new_callable=AsyncMock, return_value=None), \
         patch("taskbrew.dashboard.routers.usage._fetch_usage_via_cli", new_callable=AsyncMock, return_value=None):
        resp = await client.get("/api/usage/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["plan"] is None
    assert data["plan_limits"] is None


@pytest.mark.asyncio
async def test_usage_summary_parses_session(client, sample_stats, sample_session_jsonl):
    stats_file = sample_session_jsonl / "stats.json"
    stats_file.write_text(json.dumps(sample_stats))

    with patch("taskbrew.dashboard.routers.usage.STATS_CACHE", stats_file), \
         patch("taskbrew.dashboard.routers.usage.PROJECTS_DIR", sample_session_jsonl / "projects"), \
         patch("taskbrew.dashboard.routers.usage._fetch_profile", new_callable=AsyncMock, return_value=MOCK_PROFILE), \
         patch("taskbrew.dashboard.routers.usage._fetch_usage_via_cli", new_callable=AsyncMock, return_value=MOCK_PLAN_LIMITS):
        resp = await client.get("/api/usage/summary")

    data = resp.json()
    assert data["available"] is True
    s = data["session"]
    assert s is not None
    assert s["messages"] == 2
    assert s["output_tokens"] == 1300  # 500 + 800
    assert s["input_tokens"] == 300    # 100 + 200
    assert len(s["models"]) == 1
    assert s["models"][0]["display_name"] == "Opus 4.6"


@pytest.mark.asyncio
async def test_usage_week_models_have_color_and_percentage(client, sample_stats, tmp_path):
    stats_file = tmp_path / "stats-cache.json"
    stats_file.write_text(json.dumps(sample_stats))

    with patch("taskbrew.dashboard.routers.usage.STATS_CACHE", stats_file), \
         patch("taskbrew.dashboard.routers.usage.PROJECTS_DIR", tmp_path / "no-projects"), \
         patch("taskbrew.dashboard.routers.usage._fetch_profile", new_callable=AsyncMock, return_value=None), \
         patch("taskbrew.dashboard.routers.usage._fetch_usage_via_cli", new_callable=AsyncMock, return_value=None):
        resp = await client.get("/api/usage/summary")

    data = resp.json()
    for m in data["week"]["models"]:
        assert "color" in m
        assert "percentage" in m
        assert "display_name" in m


@pytest.mark.asyncio
async def test_usage_sonnet_tracking(client, tmp_path):
    stats = {
        "version": 2,
        "lastComputedDate": "2026-02-26",
        "dailyActivity": [],
        "dailyModelTokens": [
            {
                "date": "2026-02-24",
                "tokensByModel": {
                    "claude-opus-4-6": 100000,
                    "claude-sonnet-4-6": 50000,
                    "claude-sonnet-4-5-20250929": 30000,
                },
            },
        ],
        "modelUsage": {},
        "totalSessions": 1,
        "totalMessages": 10,
    }
    stats_file = tmp_path / "stats-cache.json"
    stats_file.write_text(json.dumps(stats))

    with patch("taskbrew.dashboard.routers.usage.STATS_CACHE", stats_file), \
         patch("taskbrew.dashboard.routers.usage.PROJECTS_DIR", tmp_path / "no-projects"), \
         patch("taskbrew.dashboard.routers.usage._fetch_profile", new_callable=AsyncMock, return_value=None), \
         patch("taskbrew.dashboard.routers.usage._fetch_usage_via_cli", new_callable=AsyncMock, return_value=None):
        resp = await client.get("/api/usage/summary")

    data = resp.json()
    assert data["week"]["sonnet_tokens"] == 80000  # 50000 + 30000
    assert data["week"]["sonnet_percentage"] > 0


def test_parse_usage_text():
    """Test the ANSI-stripped /usage output parser."""
    from taskbrew.dashboard.routers.usage import _parse_usage_text

    sample = (
        "Settings: Status Config Usage Loading usage data "
        "Current session ███▌ 7% used Resets 1:30pm (Asia/Calcutta) "
        "Current week (all models) ▌ 1% used Resets Mar 6 at 8:30am (Asia/Calcutta) "
        "Current week (Sonnet only) 0% used "
        "Extra usage ██████████████████████████████████████████████████ 100% used "
        "$68.38 / $50.00 spent · Resets Mar 1 (Asia/Calcutta) "
        "Esc to cancel"
    )
    result = _parse_usage_text(sample)
    assert len(result["limits"]) == 4
    assert result["limits"][0]["label"] == "Current session"
    assert result["limits"][0]["pct_used"] == 7
    assert "1:30pm" in result["limits"][0]["resets"]
    assert result["limits"][1]["pct_used"] == 1
    assert result["limits"][2]["label"] == "Current week (Sonnet only)"
    assert result["limits"][2]["pct_used"] == 0
    assert result["limits"][3]["pct_used"] == 100
    assert result["extra_usage_spent"] == 68.38
    assert result["extra_usage_limit"] == 50.00
