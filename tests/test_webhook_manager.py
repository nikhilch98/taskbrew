"""Tests for the WebhookManager."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.webhook_manager import WebhookManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def wh_mgr(db: Database) -> WebhookManager:
    """Create a WebhookManager backed by the in-memory database."""
    return WebhookManager(db)


# ------------------------------------------------------------------
# Registration tests
# ------------------------------------------------------------------


async def test_create_webhook(wh_mgr: WebhookManager):
    """create_webhook returns correct fields including id, url, events."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed", "task.failed"],
        secret="mysecret",
    )

    assert "id" in wh
    assert wh["url"] == "https://example.com/hook"
    assert wh["events"] == ["task.completed", "task.failed"]


async def test_create_webhook_without_secret(wh_mgr: WebhookManager, db: Database):
    """create_webhook with no secret stores NULL in the database."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["*"],
    )
    row = await db.execute_fetchone(
        "SELECT * FROM webhooks WHERE id = ?", (wh["id"],)
    )
    assert row["secret"] is None


async def test_create_multiple_webhooks(wh_mgr: WebhookManager):
    """Multiple webhooks can be registered and all appear in get_webhooks."""
    await wh_mgr.create_webhook(url="https://a.com/hook", events=["e1"])
    await wh_mgr.create_webhook(url="https://b.com/hook", events=["e2"])
    await wh_mgr.create_webhook(url="https://c.com/hook", events=["e3"])

    webhooks = await wh_mgr.get_webhooks()
    assert len(webhooks) == 3
    urls = {w["url"] for w in webhooks}
    assert urls == {"https://a.com/hook", "https://b.com/hook", "https://c.com/hook"}


# ------------------------------------------------------------------
# List webhooks
# ------------------------------------------------------------------


async def test_list_webhooks_empty(wh_mgr: WebhookManager):
    """get_webhooks returns an empty list when none exist."""
    webhooks = await wh_mgr.get_webhooks()
    assert webhooks == []


async def test_list_webhooks_returns_only_active(wh_mgr: WebhookManager, db: Database):
    """get_webhooks only returns webhooks with active=1."""
    wh = await wh_mgr.create_webhook(url="https://a.com/hook", events=["*"])

    # Deactivate via direct DB update
    await db.execute(
        "UPDATE webhooks SET active = 0 WHERE id = ?", (wh["id"],)
    )

    webhooks = await wh_mgr.get_webhooks()
    assert len(webhooks) == 0


# ------------------------------------------------------------------
# Delete webhook
# ------------------------------------------------------------------


async def test_delete_webhook(wh_mgr: WebhookManager):
    """Deleted webhook disappears from the list."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["*"],
    )

    await wh_mgr.delete_webhook(wh["id"])

    webhooks = await wh_mgr.get_webhooks()
    assert len(webhooks) == 0


async def test_delete_nonexistent_webhook(wh_mgr: WebhookManager):
    """Deleting a webhook that does not exist is a silent no-op."""
    await wh_mgr.delete_webhook("does-not-exist")
    webhooks = await wh_mgr.get_webhooks()
    assert len(webhooks) == 0


# ------------------------------------------------------------------
# Trigger webhook (fire) -- mock HTTP call
# ------------------------------------------------------------------


async def test_fire_sends_post_to_matching_webhook(wh_mgr: WebhookManager):
    """fire() POSTs to webhooks whose events include the fired event type."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
        secret="s3cret",
    )

    # Mock the aiohttp session
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)

    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})

    # Allow the asyncio.create_task to run
    await asyncio.sleep(0.1)

    # Verify POST was called
    mock_session.post.assert_called_once()
    call_args = mock_session.post.call_args

    # Check URL
    assert call_args[0][0] == "https://example.com/hook"

    # Check payload structure
    sent_payload = json.loads(call_args[1]["data"])
    assert sent_payload["event"] == "task.completed"
    assert sent_payload["data"] == {"task_id": "CD-001"}
    assert "timestamp" in sent_payload

    # Check headers include content type and signature
    headers = call_args[1]["headers"]
    assert headers["Content-Type"] == "application/json"
    assert "X-Webhook-Signature" in headers


async def test_fire_wildcard_matches_any_event(wh_mgr: WebhookManager):
    """Webhook subscribed to '*' receives all event types."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["*"],
    )

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("any.random.event", {"key": "val"})
    await asyncio.sleep(0.1)

    mock_session.post.assert_called_once()
    sent_payload = json.loads(mock_session.post.call_args[1]["data"])
    assert sent_payload["event"] == "any.random.event"


async def test_fire_no_match_does_not_post(wh_mgr: WebhookManager):
    """fire() does not POST when no webhook matches the event type."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
    )

    mock_session = AsyncMock()
    wh_mgr._session = mock_session

    await wh_mgr.fire("agent.started", {"agent": "coder-1"})
    await asyncio.sleep(0.1)

    mock_session.post.assert_not_called()


async def test_fire_without_session_is_noop(wh_mgr: WebhookManager):
    """fire() returns silently when no HTTP session is open."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["*"],
    )

    # _session is None by default (start() not called)
    assert wh_mgr._session is None
    # This should not raise
    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})


async def test_fire_updates_last_triggered_at(wh_mgr: WebhookManager, db: Database):
    """After a successful fire, last_triggered_at is updated in the DB."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
    )

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    row = await db.execute_fetchone(
        "SELECT last_triggered_at FROM webhooks WHERE id = ?", (wh["id"],)
    )
    assert row["last_triggered_at"] is not None


# ------------------------------------------------------------------
# Signature verification
# ------------------------------------------------------------------


async def test_hmac_signature_in_header(wh_mgr: WebhookManager):
    """_send includes correct HMAC-SHA256 signature in X-Webhook-Signature header."""
    secret = "test-secret-key"
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
        secret=secret,
    )

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    call_args = mock_session.post.call_args
    payload_str = call_args[1]["data"]
    headers = call_args[1]["headers"]

    # Recompute the expected signature
    expected_sig = hmac.new(
        secret.encode(), payload_str.encode(), hashlib.sha256
    ).hexdigest()
    assert headers["X-Webhook-Signature"] == expected_sig


async def test_no_signature_when_no_secret(wh_mgr: WebhookManager):
    """_send omits X-Webhook-Signature header when webhook has no secret."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
        # no secret
    )

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    headers = mock_session.post.call_args[1]["headers"]
    assert "X-Webhook-Signature" not in headers


async def test_signature_deterministic():
    """Same secret + payload always produces the same HMAC signature."""
    secret = "deterministic-key"
    payload = json.dumps({"event": "test", "data": {}})

    sig1 = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    sig2 = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    assert sig1 == sig2
    assert len(sig1) == 64


async def test_different_secret_different_signature():
    """Different secrets produce different signatures for the same payload."""
    payload = json.dumps({"event": "test", "data": {}})

    sig_a = hmac.new(b"secret-a", payload.encode(), hashlib.sha256).hexdigest()
    sig_b = hmac.new(b"secret-b", payload.encode(), hashlib.sha256).hexdigest()
    assert sig_a != sig_b


# ------------------------------------------------------------------
# Deactivate / activate webhook
# ------------------------------------------------------------------


async def test_deactivate_webhook(wh_mgr: WebhookManager, db: Database):
    """Setting active=0 removes the webhook from get_webhooks results."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
    )

    # Deactivate
    await db.execute(
        "UPDATE webhooks SET active = 0 WHERE id = ?", (wh["id"],)
    )

    # Should not appear in active list
    active = await wh_mgr.get_webhooks()
    assert len(active) == 0

    # But should still exist in DB
    all_wh = await db.execute_fetchall("SELECT * FROM webhooks")
    assert len(all_wh) == 1
    assert all_wh[0]["active"] == 0


async def test_activate_webhook(wh_mgr: WebhookManager, db: Database):
    """Re-activating a deactivated webhook makes it appear in get_webhooks again."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
    )

    # Deactivate then reactivate
    await db.execute(
        "UPDATE webhooks SET active = 0 WHERE id = ?", (wh["id"],)
    )
    await db.execute(
        "UPDATE webhooks SET active = 1 WHERE id = ?", (wh["id"],)
    )

    active = await wh_mgr.get_webhooks()
    assert len(active) == 1
    assert active[0]["url"] == "https://example.com/hook"


async def test_deactivated_webhook_not_fired(wh_mgr: WebhookManager, db: Database):
    """fire() skips deactivated webhooks even if events match."""
    wh = await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["*"],
    )

    # Deactivate
    await db.execute(
        "UPDATE webhooks SET active = 0 WHERE id = ?", (wh["id"],)
    )

    mock_session = AsyncMock()
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    mock_session.post.assert_not_called()


# ------------------------------------------------------------------
# Filter by event type
# ------------------------------------------------------------------


async def test_filter_exact_event_match(wh_mgr: WebhookManager):
    """Only webhooks subscribed to the exact event type are triggered."""
    await wh_mgr.create_webhook(url="https://a.com/hook", events=["task.completed"])
    await wh_mgr.create_webhook(url="https://b.com/hook", events=["task.failed"])

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    # Only webhook a (task.completed) should be called
    assert mock_session.post.call_count == 1
    called_url = mock_session.post.call_args[0][0]
    assert called_url == "https://a.com/hook"


async def test_filter_multiple_events_on_one_webhook(wh_mgr: WebhookManager):
    """A webhook subscribed to multiple events fires for any matching event."""
    await wh_mgr.create_webhook(
        url="https://multi.com/hook",
        events=["task.completed", "task.failed", "agent.started"],
    )

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.failed", {"task_id": "CD-002"})
    await asyncio.sleep(0.1)

    assert mock_session.post.call_count == 1
    sent_payload = json.loads(mock_session.post.call_args[1]["data"])
    assert sent_payload["event"] == "task.failed"


async def test_filter_wildcard_plus_specific(wh_mgr: WebhookManager):
    """Both wildcard and specifically-subscribed webhooks fire for the same event."""
    await wh_mgr.create_webhook(url="https://wild.com/hook", events=["*"])
    await wh_mgr.create_webhook(url="https://spec.com/hook", events=["task.completed"])

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    # Both webhooks should be called
    assert mock_session.post.call_count == 2


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


async def test_send_logs_warning_on_http_error(wh_mgr: WebhookManager):
    """_send logs a warning when the remote returns an HTTP error status."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
    )

    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_response)
    wh_mgr._session = mock_session

    # Should not raise -- errors are logged, not propagated
    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)

    # POST was still attempted
    mock_session.post.assert_called_once()


async def test_send_handles_network_exception(wh_mgr: WebhookManager):
    """_send catches exceptions (e.g. connection refused) without crashing."""
    await wh_mgr.create_webhook(
        url="https://example.com/hook",
        events=["task.completed"],
    )

    mock_session = AsyncMock()
    mock_session.post = MagicMock(side_effect=ConnectionError("refused"))
    wh_mgr._session = mock_session

    # Should not raise
    await wh_mgr.fire("task.completed", {"task_id": "CD-001"})
    await asyncio.sleep(0.1)


# ------------------------------------------------------------------
# Start / stop lifecycle
# ------------------------------------------------------------------


async def test_start_without_aiohttp_is_noop(wh_mgr: WebhookManager):
    """start() is a no-op when aiohttp is not installed."""
    await wh_mgr.start()
    # aiohttp is not installed in the test env, so session stays None
    assert wh_mgr._session is None


async def test_stop_closes_session(wh_mgr: WebhookManager):
    """stop() closes the session and sets it to None."""
    mock_session = AsyncMock()
    wh_mgr._session = mock_session

    await wh_mgr.stop()
    assert wh_mgr._session is None
    mock_session.close.assert_awaited_once()


async def test_stop_when_no_session_is_noop(wh_mgr: WebhookManager):
    """stop() is a no-op when no session has been started."""
    assert wh_mgr._session is None
    await wh_mgr.stop()  # Should not raise
    assert wh_mgr._session is None
