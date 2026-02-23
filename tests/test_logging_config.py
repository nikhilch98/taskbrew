"""Tests for logging configuration."""

import logging
from taskbrew.logging_config import setup_logging


def test_invalid_log_level_falls_back_to_info(capsys):
    """Invalid LOG_LEVEL should warn and fall back to INFO."""
    setup_logging(level="BOGUS")
    captured = capsys.readouterr()
    assert "Invalid LOG_LEVEL" in captured.err
    assert logging.root.level == logging.INFO


def test_valid_log_level_works():
    """Valid LOG_LEVEL should be applied."""
    setup_logging(level="DEBUG")
    assert logging.root.level == logging.DEBUG
    # Reset
    setup_logging(level="INFO")
