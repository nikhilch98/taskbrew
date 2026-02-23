"""Tests for the AuthManager."""

from __future__ import annotations

from unittest.mock import MagicMock


from taskbrew.auth import AuthManager


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_request(authorization: str | None = None) -> MagicMock:
    """Create a mock Starlette/FastAPI Request with optional Authorization header."""
    request = MagicMock()
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    request.headers = headers
    return request


# ------------------------------------------------------------------
# Generate token
# ------------------------------------------------------------------


def test_generate_token_returns_string():
    """generate_token returns a non-empty string."""
    auth = AuthManager(enabled=True, tokens=["seed"])
    token = auth.generate_token()
    assert isinstance(token, str)
    assert len(token) > 0


def test_generate_token_unique():
    """Each call to generate_token returns a different value."""
    auth = AuthManager(enabled=True, tokens=["seed"])
    t1 = auth.generate_token()
    t2 = auth.generate_token()
    assert t1 != t2


# ------------------------------------------------------------------
# Verify valid token
# ------------------------------------------------------------------


def test_verify_valid_token_returns_true():
    """verify() returns True for a request carrying a valid bearer token."""
    auth = AuthManager(enabled=True, tokens=["my-secret-token"])
    request = _make_request(authorization="Bearer my-secret-token")
    assert auth.verify(request) is True


def test_verify_generated_token_returns_true():
    """A freshly generated token is accepted by verify()."""
    auth = AuthManager(enabled=True, tokens=["initial"])
    new_token = auth.generate_token()
    request = _make_request(authorization=f"Bearer {new_token}")
    assert auth.verify(request) is True


def test_verify_multiple_valid_tokens():
    """All pre-configured tokens are accepted."""
    auth = AuthManager(enabled=True, tokens=["token-a", "token-b", "token-c"])

    for t in ("token-a", "token-b", "token-c"):
        request = _make_request(authorization=f"Bearer {t}")
        assert auth.verify(request) is True


# ------------------------------------------------------------------
# Verify invalid / expired token
# ------------------------------------------------------------------


def test_verify_invalid_token_returns_false():
    """verify() returns False for a request with an unrecognised token."""
    auth = AuthManager(enabled=True, tokens=["valid-token"])
    request = _make_request(authorization="Bearer wrong-token")
    assert auth.verify(request) is False


def test_verify_non_bearer_scheme_returns_false():
    """verify() returns False when Authorization uses a non-Bearer scheme."""
    auth = AuthManager(enabled=True, tokens=["valid-token"])
    request = _make_request(authorization="Basic dXNlcjpwYXNz")
    assert auth.verify(request) is False


def test_verify_empty_authorization_returns_false():
    """verify() returns False for an empty Authorization header."""
    auth = AuthManager(enabled=True, tokens=["valid-token"])
    request = _make_request(authorization="")
    assert auth.verify(request) is False


# ------------------------------------------------------------------
# Revoke token makes it invalid
# ------------------------------------------------------------------


def test_revoke_token_makes_it_invalid():
    """After revoking a token, verify() returns False for that token."""
    auth = AuthManager(enabled=True, tokens=["initial-token"])

    new_token = auth.generate_token()
    request = _make_request(authorization=f"Bearer {new_token}")
    assert auth.verify(request) is True

    auth.revoke_token(new_token)
    assert auth.verify(request) is False


def test_revoke_does_not_affect_other_tokens():
    """Revoking one token does not invalidate other tokens."""
    auth = AuthManager(enabled=True, tokens=["token-a", "token-b"])

    auth.revoke_token("token-a")

    req_a = _make_request(authorization="Bearer token-a")
    assert auth.verify(req_a) is False

    req_b = _make_request(authorization="Bearer token-b")
    assert auth.verify(req_b) is True


def test_revoke_nonexistent_token_is_noop():
    """Revoking a token that was never added does not raise."""
    auth = AuthManager(enabled=True, tokens=["real-token"])
    auth.revoke_token("nonexistent")  # Should not raise

    request = _make_request(authorization="Bearer real-token")
    assert auth.verify(request) is True


# ------------------------------------------------------------------
# Verify with no token
# ------------------------------------------------------------------


def test_verify_with_no_token_returns_false():
    """verify() returns False when no Authorization header is present at all."""
    auth = AuthManager(enabled=True, tokens=["valid-token"])
    request = _make_request()  # No Authorization header
    assert auth.verify(request) is False


def test_verify_with_bearer_prefix_only_returns_false():
    """verify() returns False when Authorization is 'Bearer ' with an empty token."""
    auth = AuthManager(enabled=True, tokens=["valid-token"])
    request = _make_request(authorization="Bearer ")
    assert auth.verify(request) is False


# ------------------------------------------------------------------
# Disabled auth
# ------------------------------------------------------------------


def test_disabled_allows_all():
    """When auth is disabled, verify() always returns True regardless of token."""
    auth = AuthManager(enabled=False)

    # No token
    request = _make_request()
    assert auth.verify(request) is True

    # Invalid token
    request = _make_request(authorization="Bearer invalid-token")
    assert auth.verify(request) is True

    # No Authorization header at all
    request = _make_request(authorization=None)
    assert auth.verify(request) is True


# ------------------------------------------------------------------
# Auto-generated token on init
# ------------------------------------------------------------------


def test_auto_generate_token_when_enabled_without_tokens():
    """Enabling auth without tokens auto-generates one that works."""
    auth = AuthManager(enabled=True)
    # Should have exactly one auto-generated token hash
    assert len(auth._tokens) == 1

    # The raw token is stored on the instance for programmatic retrieval
    assert auth.auto_generated_token is not None
    request = _make_request(authorization=f"Bearer {auth.auto_generated_token}")
    assert auth.verify(request) is True


def test_auto_generated_token_not_fully_logged(caplog):
    """Auto-generated token should not appear fully in log output."""
    import logging

    with caplog.at_level(logging.WARNING, logger="taskbrew.auth"):
        auth = AuthManager(enabled=True)

    token = auth.auto_generated_token
    assert token is not None

    # The first 8 chars should appear in the log (as a prefix indicator)
    assert token[:8] in caplog.text

    # The full token should NOT appear in the log output
    assert token not in caplog.text
