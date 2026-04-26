"""Tests for startup validation."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def test_validate_startup_missing_cli():
    """Should error when CLI binary is missing."""
    from taskbrew.main import StartupValidationError, _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(StartupValidationError):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="claude",
                )


def test_validate_startup_no_roles():
    """No roles should warn but not block dashboard startup."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles={},
                cli_provider="claude",
            )


def test_validate_startup_passes():
    """Should not raise when CLI is found and roles exist."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            # Should not raise — no API key needed
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles={"pm": "dummy"},
                cli_provider="claude",
            )


def test_validate_startup_gemini_missing_cli():
    """Should error when Gemini CLI binary is missing."""
    from taskbrew.main import StartupValidationError, _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(StartupValidationError):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="gemini",
                )


def test_validate_startup_gemini_passes():
    """Should not raise when Gemini CLI is found and roles exist."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/gemini"):
            # Should not raise — no API key needed
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles={"pm": "dummy"},
                cli_provider="gemini",
            )


def test_validate_startup_codex_missing_cli():
    """Should error when Codex CLI binary is missing."""
    from taskbrew.main import StartupValidationError, _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(StartupValidationError):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="codex",
                )


def test_validate_startup_codex_passes():
    """Should not raise when Codex CLI is found and roles exist."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/codex"):
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles={"pm": "dummy"},
                cli_provider="codex",
            )


def test_validate_startup_checks_role_model_provider():
    """Role models can require a different CLI than team default."""
    from taskbrew.main import StartupValidationError, _validate_startup

    def fake_which(name):
        return "/usr/bin/claude" if name == "claude" else None

    roles = {"coder": SimpleNamespace(model="gpt-5.2")}
    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", side_effect=fake_which):
            with pytest.raises(StartupValidationError, match="Codex CLI not found"):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles=roles,
                    cli_provider="claude",
                )


def test_validate_startup_does_not_require_unused_default_provider():
    """A Codex role should not require Claude just because it is the fallback."""
    from taskbrew.main import _validate_startup

    def fake_which(name):
        return "/usr/bin/codex" if name == "codex" else None

    roles = {"coder": SimpleNamespace(model="gpt-5.2")}
    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", side_effect=fake_which):
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles=roles,
                cli_provider="claude",
            )


def test_validate_startup_multiple_errors():
    """Should accumulate multiple errors before failing."""
    from taskbrew.main import StartupValidationError, _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(StartupValidationError):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={},
                    cli_provider="claude",
                )
