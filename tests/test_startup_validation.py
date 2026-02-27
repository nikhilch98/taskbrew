"""Tests for startup validation."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest


def test_validate_startup_missing_api_key():
    """Should error when API key is missing."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with pytest.raises(SystemExit):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="claude",
                )


def test_validate_startup_missing_cli():
    """Should error when CLI binary is missing."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="claude",
                )


def test_validate_startup_no_roles():
    """Should error when no roles are configured."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            with pytest.raises(SystemExit):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={},
                    cli_provider="claude",
                )


def test_validate_startup_passes():
    """Should not raise when everything is configured."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            # Should not raise
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles={"pm": "dummy"},
                cli_provider="claude",
            )


def test_validate_startup_gemini_missing_api_key():
    """Should error when Gemini API key is missing."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/gemini"):
            with pytest.raises(SystemExit):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="gemini",
                )


def test_validate_startup_gemini_missing_cli():
    """Should error when Gemini CLI binary is missing."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={"pm": "dummy"},
                    cli_provider="gemini",
                )


def test_validate_startup_gemini_passes():
    """Should not raise when Gemini is fully configured."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}, clear=True):
        with patch("shutil.which", return_value="/usr/bin/gemini"):
            # Should not raise
            _validate_startup(
                project_dir=Path("/tmp"),
                team_config=None,
                roles={"pm": "dummy"},
                cli_provider="gemini",
            )


def test_validate_startup_multiple_errors():
    """Should accumulate multiple errors before failing."""
    from taskbrew.main import _validate_startup

    with patch.dict(os.environ, {}, clear=True):
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                _validate_startup(
                    project_dir=Path("/tmp"),
                    team_config=None,
                    roles={},
                    cli_provider="claude",
                )
