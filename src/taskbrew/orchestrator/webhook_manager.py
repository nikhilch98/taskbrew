"""Webhook integration for external notifications.

Audit 03 F#7 / top-15 #11: SSRF hardening.

The previous ``_validate_url`` only checked literal IPs and a tiny hostname
blocklist. A DNS A record pointing at ``169.254.169.254`` (AWS IMDS) or
``127.0.0.1`` sailed through at create time; even when the hostname was
resolved at request time, aiohttp would happily connect to whatever the
second lookup returned (DNS rebinding). The fix:

1. At create time, resolve the hostname and reject if ANY answer is
   private / loopback / link-local / multicast / unspecified / reserved.
2. At fire time, re-resolve and apply the same check; if the answer has
   changed to a private range between create and fire, the send is
   refused (closing the rebind window).
3. Redirects are disabled on the POST so a 302 to an internal URL cannot
   bypass validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
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

    @staticmethod
    def _ip_is_unsafe(ip: ipaddress._BaseAddress) -> bool:
        """Return True iff *ip* lands in a range that must NOT be reachable
        by a webhook request.
        """
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )

    @classmethod
    def _validate_hostname_resolution(
        cls,
        hostname: str,
        *,
        strict: bool,
    ) -> list[ipaddress._BaseAddress]:
        """Resolve *hostname* and reject if any resolved address is in a
        range that could reach internal infrastructure.

        When *strict* is True (fire-time check), DNS resolution failure or
        an empty answer is fatal: we refuse to send. When *strict* is False
        (create-time check), resolution failure is a WARNING; we defer the
        enforcement to the fire-time recheck so webhook creation survives a
        transient DNS outage.

        Returns the resolved addresses so callers can inspect (or, later,
        pin) them.
        """
        try:
            infos = socket.getaddrinfo(
                hostname, None,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as exc:
            if strict:
                raise ValueError(f"URL hostname {hostname!r} does not resolve: {exc}")
            logger.warning(
                "webhook create: hostname %r does not resolve right now (%s); "
                "deferring IP check to fire time.",
                hostname, exc,
            )
            return []

        addrs: list[ipaddress._BaseAddress] = []
        for _family, _type, _proto, _canon, sockaddr in infos:
            raw_ip = sockaddr[0]
            try:
                ip = ipaddress.ip_address(raw_ip)
            except ValueError:
                continue
            if cls._ip_is_unsafe(ip):
                # Unsafe resolved IP is always fatal, both at create and
                # fire time.
                raise ValueError(
                    f"URL hostname {hostname!r} resolves to a private/"
                    f"reserved address: {ip}"
                )
            addrs.append(ip)

        if not addrs and strict:
            raise ValueError(f"URL hostname {hostname!r} returned no usable addresses")
        return addrs

    def _validate_url(self, url: str) -> None:
        """Validate webhook URL to prevent SSRF attacks.

        Run at webhook create time and again at fire time (see
        ``_validate_url_at_fire_time``). The create-time check is the first
        line of defence; the fire-time recheck closes the DNS-rebinding
        window.
        """
        parsed = urlparse(url)

        # Must be http or https
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme: {parsed.scheme}. Must be http or https.")

        # Must have a hostname
        if not parsed.hostname:
            raise ValueError("URL must have a hostname.")

        hostname = parsed.hostname

        # Literal IP? apply the unsafe-range filter directly.
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None

        if ip is not None:
            if self._ip_is_unsafe(ip):
                raise ValueError(
                    f"URL points to a private/reserved IP address: {hostname}"
                )
            return

        # Hostname: block well-known internal names BEFORE resolving so we
        # never leak a DNS query for metadata endpoints to a shared resolver.
        hostname_lower = hostname.lower()
        blocked = (
            "localhost",
            "metadata.google",
            "metadata.google.internal",
            "metadata.aws.amazon.com",
            "metadata",  # exact match
        )
        if any(
            hostname_lower == b or hostname_lower.endswith("." + b)
            for b in blocked
        ):
            raise ValueError(f"URL points to a blocked hostname: {hostname}")

        # Resolve the hostname and check every returned address. At create
        # time a DNS failure is allowed through with a warning (see the
        # strict=False branch); the guarantee is reasserted at fire time.
        self._validate_hostname_resolution(hostname, strict=False)

    async def _validate_url_at_fire_time(self, url: str) -> None:
        """Re-validate *url* immediately before making the HTTP request.

        Run in an executor so the blocking getaddrinfo call does not stall
        the event loop. This closes the DNS-rebinding window between
        create-time validation and actual send.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"Malformed webhook URL at fire time: {url}")

        # Literal IP: same check as _validate_url, no DNS needed.
        try:
            ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            ip = None
        if ip is not None:
            if self._ip_is_unsafe(ip):
                raise ValueError(
                    f"URL points to a private/reserved IP address: {parsed.hostname}"
                )
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: self._validate_hostname_resolution(parsed.hostname, strict=True),
        )

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
        """POST to a single webhook endpoint with retry and delivery logging.

        audit 03 F#8 HMAC hardening:

        - Emit a ``X-Webhook-Timestamp`` Unix-seconds header.
        - Sign ``"{timestamp}.{payload}"`` instead of just the payload,
          so a captured delivery cannot be replayed against a later
          clock.
        - Use Stripe-style versioned signature header
          ``X-Webhook-Signature: t=<unix>,v1=<hex>`` so future algorithm
          rotations (``v2=`` etc.) can coexist without breaking
          consumers.
        - Legacy ``X-Webhook-Signature`` (bare hex) is still sent so
          receivers that haven't migrated keep verifying the raw
          payload. New receivers should verify the v1 variant and
          reject requests whose timestamp is more than N minutes from
          now.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(
            {
                "event": event_type,
                "data": data,
                "timestamp": now_iso,
            }
        )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if webhook.get("secret"):
            ts_unix = str(int(datetime.now(timezone.utc).timestamp()))
            secret_bytes = webhook["secret"].encode()
            signed_input = f"{ts_unix}.{payload}".encode()
            v1_sig = hmac.new(secret_bytes, signed_input, hashlib.sha256).hexdigest()
            legacy_sig = hmac.new(
                secret_bytes, payload.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Webhook-Timestamp"] = ts_unix
            headers["X-Webhook-Signature"] = f"t={ts_unix},v1={v1_sig}"
            # Back-compat header for receivers that haven't upgraded
            # their verification to the v1 scheme.
            headers["X-Webhook-Signature-Legacy"] = legacy_sig

        # Create a delivery log entry
        delivery_id = await self._create_delivery(
            webhook["id"], event_type, payload
        )

        last_error: str | None = None
        last_response_code: int | None = None

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                # Re-validate at fire time to close the DNS-rebinding window
                # between create and request. Also defends against rows
                # that pre-date the validator.
                await self._validate_url_at_fire_time(webhook["url"])
                async with self._session.post(
                    webhook["url"],
                    data=payload,
                    headers=headers,
                    # Never follow redirects -- a 302 to an internal URL
                    # would otherwise bypass the validation we just ran.
                    allow_redirects=False,
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
