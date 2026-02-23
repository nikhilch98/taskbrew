"""Webhook integration for external notifications."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    import aiohttp

    _HAS_AIOHTTP = True
except ImportError:  # pragma: no cover
    _HAS_AIOHTTP = False

# Retry configuration: exponential backoff delays in seconds.
# 3 attempts total with delays of 1s, 4s, 16s between retries.
_MAX_ATTEMPTS = 3
_RETRY_DELAYS = [1, 4, 16]


class WebhookManager:
    """Manage webhooks and fire HTTP callbacks on events."""

    def __init__(self, db) -> None:
        self._db = db
        self._session: aiohttp.ClientSession | None = None if _HAS_AIOHTTP else None

    async def start(self) -> None:
        """Open the shared HTTP client session."""
        if _HAS_AIOHTTP:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def stop(self) -> None:
        """Close the shared HTTP client session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _validate_url(self, url: str) -> None:
        """Validate webhook URL to prevent SSRF attacks."""
        parsed = urlparse(url)

        # Must be http or https
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}. Must be http or https.")

        # Must have a hostname
        if not parsed.hostname:
            raise ValueError("URL must have a hostname.")

        # Block private/reserved IP ranges
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"URL points to a private/reserved IP address: {parsed.hostname}")
        except ValueError as e:
            if "private" in str(e) or "reserved" in str(e) or "loopback" in str(e):
                raise
            # Not an IP address — it's a hostname, which is fine
            # But block common internal hostnames
            hostname_lower = parsed.hostname.lower()
            blocked = ("localhost", "127.0.0.1", "0.0.0.0", "metadata.google", "169.254.169.254")
            if any(hostname_lower == b or hostname_lower.endswith("." + b) for b in blocked):
                raise ValueError(f"URL points to a blocked hostname: {parsed.hostname}")

    async def get_webhooks(self) -> list[dict]:
        """Return all active webhooks."""
        return await self._db.execute_fetchall(
            "SELECT * FROM webhooks WHERE active = 1"
        )

    async def create_webhook(
        self, url: str, events: list[str], secret: str | None = None
    ) -> dict:
        """Register a new webhook.

        Parameters
        ----------
        url:
            The endpoint to POST to.
        events:
            List of event types to subscribe to (e.g. ``['task_completed', '*']``).
        secret:
            Optional HMAC-SHA256 secret for payload signing.
        """
        self._validate_url(url)
        webhook_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO webhooks (id, url, events, secret, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (webhook_id, url, ",".join(events), secret, now),
        )
        return {"id": webhook_id, "url": url, "events": events}

    async def delete_webhook(self, webhook_id: str) -> None:
        """Delete a webhook by ID."""
        await self._db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))

    async def fire(self, event_type: str, data: dict) -> None:
        """Fire webhooks matching *event_type*."""
        if not self._session:
            return
        webhooks = await self._db.execute_fetchall(
            "SELECT * FROM webhooks WHERE active = 1"
        )
        for wh in webhooks:
            events = wh["events"].split(",")
            if event_type in events or "*" in events:
                asyncio.create_task(self._send(wh, event_type, data))

    async def _create_delivery(
        self, webhook_id: str, event_type: str, payload: str
    ) -> str:
        """Insert a new pending delivery record and return its ID."""
        delivery_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO webhook_deliveries "
            "(id, webhook_id, event_type, payload, status, attempt_count, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?)",
            (delivery_id, webhook_id, event_type, payload, now),
        )
        return delivery_id

    async def _update_delivery(
        self,
        delivery_id: str,
        *,
        status: str,
        attempt_count: int,
        response_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update a delivery record after an attempt."""
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "UPDATE webhook_deliveries "
            "SET status = ?, attempt_count = ?, response_code = ?, "
            "last_attempted_at = ?, error_message = ? "
            "WHERE id = ?",
            (status, attempt_count, response_code, now, error_message, delivery_id),
        )

    async def _send(self, webhook: dict, event_type: str, data: dict) -> None:
        """POST to a single webhook endpoint with retry and delivery logging."""
        payload = json.dumps(
            {
                "event": event_type,
                "data": data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if webhook.get("secret"):
            sig = hmac.new(
                webhook["secret"].encode(), payload.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Webhook-Signature"] = sig

        # Create a delivery log entry
        delivery_id = await self._create_delivery(
            webhook["id"], event_type, payload
        )

        last_error: str | None = None
        last_response_code: int | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                async with self._session.post(
                    webhook["url"], data=payload, headers=headers
                ) as resp:
                    last_response_code = resp.status
                    if resp.status < 400:
                        # Success
                        await self._update_delivery(
                            delivery_id,
                            status="success",
                            attempt_count=attempt,
                            response_code=resp.status,
                        )
                        await self._db.execute(
                            "UPDATE webhooks SET last_triggered_at = ? WHERE id = ?",
                            (datetime.now(timezone.utc).isoformat(), webhook["id"]),
                        )
                        return
                    else:
                        last_error = f"HTTP {resp.status}"
                        logger.warning(
                            "Webhook %s attempt %d/%d returned %d",
                            webhook["id"], attempt, _MAX_ATTEMPTS, resp.status,
                        )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_response_code = None
                logger.warning(
                    "Webhook %s attempt %d/%d failed: %s",
                    webhook["id"], attempt, _MAX_ATTEMPTS, last_error,
                )

            # If not the last attempt, wait before retrying
            if attempt < _MAX_ATTEMPTS:
                delay = _RETRY_DELAYS[attempt - 1]
                await asyncio.sleep(delay)

        # All attempts exhausted -- mark as failed
        logger.error(
            "Webhook %s delivery %s failed after %d attempts: %s",
            webhook["id"], delivery_id, _MAX_ATTEMPTS, last_error,
        )
        await self._update_delivery(
            delivery_id,
            status="failed",
            attempt_count=_MAX_ATTEMPTS,
            response_code=last_response_code,
            error_message=last_error,
        )

    async def get_delivery_log(
        self, webhook_id: str, limit: int = 50
    ) -> list[dict]:
        """Return recent delivery records for a webhook.

        Parameters
        ----------
        webhook_id:
            The webhook whose deliveries to retrieve.
        limit:
            Maximum number of records to return (default 50).

        Returns
        -------
        list[dict]
            Delivery records ordered by most recent first.
        """
        return await self._db.execute_fetchall(
            "SELECT * FROM webhook_deliveries "
            "WHERE webhook_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (webhook_id, limit),
        )
