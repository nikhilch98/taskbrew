# tests/conftest.py
import os
import pytest

# audit 10 F#1/F#3 test shim:
# Production default for AUTH_ENABLED is now True (fail-closed). The existing
# test suite was written before the default flip and calls every endpoint
# without Authorization headers. Setting the env var to "false" at module
# import time keeps existing tests passing while the default-on posture
# protects production deployments. Individual tests that specifically
# exercise the auth layer continue to opt in via their own fixtures.
os.environ.setdefault("AUTH_ENABLED", "false")


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory."""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "data").mkdir()
    return tmp_path
