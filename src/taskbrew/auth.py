"""Simple token-based authentication for dashboard API."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from taskbrew.orchestrator.database import Database

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages API authentication tokens with optional rate limiting.

    Parameters
    ----------
    enabled:
        Whether authentication is enforced.
    tokens:
        Optional list of pre-configured bearer tokens.
    db:
        Optional :class:`Database` instance.  When provided, token hashes are
        persisted to the ``auth_tokens`` table so they survive restarts.
        If ``None``, tokens are kept in memory only (backwards compatible).
    rate_limit_attempts:
        Number of failed attempts within ``rate_limit_window`` before the
        source IP is locked out.  Set to ``0`` to disable rate limiting.
    rate_limit_window:
        Sliding window (in seconds) during which failed attempts are counted.
    rate_limit_lockout:
        Duration (in seconds) a locked-out IP is blocked from **all**
        authentication attempts.
    """

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _persist_auto_token_to_disk(token: str):
        """Write *token* to ~/.taskbrew/auth-token.txt with 0600 perms.

        Returns the path (as a string) on success, or None on failure.
        Never raises: a failure to persist must not kill the boot. The
        operator can always read AuthManager.auto_generated_token in
        process instead.
        """
        try:
            from pathlib import Path
            import os
            path = Path.home() / ".taskbrew" / "auth-token.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            # Write atomically: write to .tmp then rename.
            tmp = path.with_suffix(".txt.tmp")
            tmp.write_text(token + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            return str(path)
        except Exception as exc:  # noqa: BLE001 -- never fatal
            logger.warning("Could not persist auto-generated token to disk: %s", exc)
            return None

    def __init__(
        self,
        enabled: bool = False,
        tokens: list[str] | None = None,
        *,
        db: Database | None = None,
        rate_limit_attempts: int = 10,
        rate_limit_window: int = 60,
        rate_limit_lockout: int = 300,
        trusted_proxies: list[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self._db = db
        self._tokens: set[str] = {self._hash_token(t) for t in (tokens or [])}
        self.auto_generated_token: str | None = None
        if enabled and not self._tokens:
            # Auto-generate a token on first run.
            # audit 01: the previous implementation print()ed the token
            # to stdout, but `taskbrew start` redirects stdout/stderr to
            # /dev/null for the daemon, so the token was unrecoverable
            # and the daemon was unreachable. Persist the full token to
            # ~/.taskbrew/auth-token.txt (0600) so the operator can find
            # it; emit only the 8-char prefix to the log so it does not
            # leak via log-forwarding pipelines.
            token = secrets.token_urlsafe(32)
            self._tokens.add(self._hash_token(token))
            self.auto_generated_token = token
            token_path = self._persist_auto_token_to_disk(token)
            if token_path is not None:
                logger.warning(
                    "AUTH ENABLED - API Token auto-generated (%s...). Full token "
                    "written to %s (chmod 600). Add header: 'Authorization: Bearer <token>'.",
                    token[:8], token_path,
                )
            else:
                logger.warning(
                    "AUTH ENABLED - API Token auto-generated (%s...). Could not "
                    "persist token to disk; read AuthManager.auto_generated_token "
                    "programmatically or configure tokens explicitly in team.yaml.",
                    token[:8],
                )

        # Rate-limiting configuration
        self.rate_limit_attempts = rate_limit_attempts
        self.rate_limit_window = rate_limit_window
        self.rate_limit_lockout = rate_limit_lockout
        # audit 01 F#2: only consult X-Forwarded-For when the deployer
        # has explicitly declared which proxies are trusted to set it.
        self.trusted_proxies = tuple(trusted_proxies or ())

        # {ip: [timestamp, ...]} – records of failed auth attempts
        self._failed_attempts: dict[str, list[float]] = {}
        # {ip: lockout_expiry_timestamp}
        self._lockouts: dict[str, float] = {}
        # Counter used to trigger periodic cleanup
        self._call_count: int = 0

    # ------------------------------------------------------------------
    # Rate-limiting helpers
    # ------------------------------------------------------------------

    def _cleanup(self, now: float) -> None:
        """Remove stale entries to bound memory growth."""
        expired_lockouts = [
            ip for ip, expiry in self._lockouts.items() if expiry <= now
        ]
        for ip in expired_lockouts:
            del self._lockouts[ip]

        cutoff = now - self.rate_limit_window
        stale_ips = [
            ip
            for ip, timestamps in self._failed_attempts.items()
            if not timestamps or timestamps[-1] < cutoff
        ]
        for ip in stale_ips:
            del self._failed_attempts[ip]

    def _is_locked_out(self, ip: str, now: float) -> bool:
        """Return ``True`` if *ip* is currently locked out."""
        expiry = self._lockouts.get(ip)
        if expiry is None:
            return False
        if now < expiry:
            return True
        # Lockout expired – clean up
        del self._lockouts[ip]
        return False

    def _record_failure(self, ip: str, now: float) -> None:
        """Record a failed attempt and lock out *ip* if the threshold is met."""
        attempts = self._failed_attempts.setdefault(ip, [])
        attempts.append(now)

        # Trim attempts outside the current window
        cutoff = now - self.rate_limit_window
        self._failed_attempts[ip] = [t for t in attempts if t > cutoff]

        if len(self._failed_attempts[ip]) >= self.rate_limit_attempts:
            self._lockouts[ip] = now + self.rate_limit_lockout
            self._failed_attempts.pop(ip, None)
            logger.warning(
                "Rate limit triggered for %s – locked out for %ds",
                ip,
                self.rate_limit_lockout,
            )

    def _clear_failures(self, ip: str) -> None:
        """Clear failure history on successful authentication."""
        self._failed_attempts.pop(ip, None)

    # ------------------------------------------------------------------
    # Database persistence helpers
    # ------------------------------------------------------------------

    async def load_tokens_from_db(self) -> None:
        """Load persisted token hashes from the database into the in-memory set.

        This is a no-op when no database is configured.
        """
        if self._db is None:
            return
        rows = await self._db.execute_fetchall(
            "SELECT token_hash FROM auth_tokens"
        )
        for row in rows:
            self._tokens.add(row["token_hash"])
        if rows:
            logger.info("Loaded %d persisted auth tokens from database", len(rows))

    async def _persist_token(self, token_hash: str) -> None:
        """Insert a token hash into the database (if configured)."""
        if self._db is None:
            return
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT OR IGNORE INTO auth_tokens (token_hash, created_at) VALUES (?, ?)",
            (token_hash, now),
        )

    async def _remove_token_from_db(self, token_hash: str) -> None:
        """Delete a token hash from the database (if configured)."""
        if self._db is None:
            return
        await self._db.execute(
            "DELETE FROM auth_tokens WHERE token_hash = ?",
            (token_hash,),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify_token_string(self, token: str | None) -> bool:
        """Return True iff *token* is a valid bearer token.

        Used by contexts that have already extracted the token from a
        carrier (e.g. an ``Authorization: Bearer ...`` header on an MCP
        endpoint) and do not have a Starlette ``Request`` to pass to
        :meth:`verify`. When authentication is disabled this always
        returns True.
        """
        if not self.enabled:
            return True
        if not token or not isinstance(token, str):
            return False
        return self._hash_token(token) in self._tokens

    def verify(self, request: Request) -> bool:
        """Return ``True`` if the request carries a valid bearer token.

        When authentication is disabled this always returns ``True``.

        If rate limiting is enabled (``rate_limit_attempts > 0``) and the
        caller's IP has exceeded the failure threshold, the request is
        rejected regardless of the token provided.
        """
        if not self.enabled:
            return True

        now = time.monotonic()

        # Periodic cleanup to prevent unbounded memory growth
        self._call_count += 1
        if self._call_count % 100 == 0:
            self._cleanup(now)

        client_ip = self._resolve_client_ip(request)

        # Check lockout **before** validating the token
        if self.rate_limit_attempts > 0 and self._is_locked_out(client_ip, now):
            logger.warning("Rejected request from locked-out IP %s", client_ip)
            return False

        # audit 01 F#2: also guard a global failure counter so an
        # attacker who rotates spoofed X-Forwarded-For values (or
        # legitimately distributed clients behind a proxy we don't
        # trust) can't bypass the per-IP lockout. A global cap kicks
        # in at attempts * 10 failures in window.
        if self.rate_limit_attempts > 0 and self._is_global_locked_out(now):
            logger.warning("Rejected request -- global auth failure budget exceeded")
            return False

        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if self._hash_token(token) in self._tokens:
                if self.rate_limit_attempts > 0:
                    self._clear_failures(client_ip)
                return True

        # Authentication failed – record if rate limiting is active
        if self.rate_limit_attempts > 0:
            self._record_failure(client_ip, now)
            self._record_global_failure(now)
        return False

    def _resolve_client_ip(self, request) -> str:
        """Pick the client IP honouring trusted-proxy config.

        audit 01 F#2: when ``trusted_proxies`` is set, parse the
        right-most element of the ``X-Forwarded-For`` chain that is
        NOT in the trusted list; otherwise fall back to the socket
        peer. Without trusted_proxies configured we intentionally do
        NOT consult forwarded headers -- they are attacker-controlled
        on direct deployments.
        """
        trusted = getattr(self, "trusted_proxies", None) or ()
        peer = getattr(getattr(request, "client", None), "host", "unknown")
        if not trusted:
            return peer
        fwd = request.headers.get("X-Forwarded-For", "").strip()
        if not fwd:
            return peer
        chain = [ip.strip() for ip in fwd.split(",") if ip.strip()]
        for ip in reversed(chain):
            if ip not in trusted:
                return ip
        return peer

    # Global failure budget state -- initialised lazily.
    def _is_global_locked_out(self, now: float) -> bool:
        expiry = getattr(self, "_global_lockout_until", 0.0)
        return now < expiry

    def _record_global_failure(self, now: float) -> None:
        attempts = getattr(self, "_global_failures", None)
        if attempts is None:
            attempts = []
            self._global_failures = attempts
        attempts.append(now)
        cutoff = now - self.rate_limit_window
        attempts[:] = [t for t in attempts if t > cutoff]
        global_cap = self.rate_limit_attempts * 10
        if global_cap > 0 and len(attempts) >= global_cap:
            self._global_lockout_until = now + self.rate_limit_lockout
            self._global_failures = []
            logger.warning(
                "Global auth failure budget exhausted -- locked out for %ds",
                self.rate_limit_lockout,
            )

    def generate_token(self) -> str:
        """Create and store a new bearer token (in-memory only).

        If a database is configured, prefer :meth:`generate_token_async`
        to also persist the token.
        """
        token = secrets.token_urlsafe(32)
        self._tokens.add(self._hash_token(token))
        return token

    async def generate_token_async(self) -> str:
        """Create, store, and persist a new bearer token.

        The token is added to the in-memory set **and** written to the
        database (when a database is configured).
        """
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        self._tokens.add(token_hash)
        await self._persist_token(token_hash)
        return token

    def revoke_token(self, token: str) -> None:
        """Remove a token so it can no longer authenticate (in-memory only).

        If a database is configured, prefer :meth:`revoke_token_async`
        to also remove the token from persistent storage.
        """
        self._tokens.discard(self._hash_token(token))

    async def revoke_token_async(self, token: str) -> None:
        """Remove a token from both memory and persistent storage."""
        token_hash = self._hash_token(token)
        self._tokens.discard(token_hash)
        await self._remove_token_from_db(token_hash)
