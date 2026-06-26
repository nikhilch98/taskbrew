"""Tests for the optional Headroom compression adapter.

Headroom is mocked throughout so these run whether or not ``headroom-ai`` is
installed. The contract under test: the adapter is default-off and fail-safe —
it returns input unchanged unless explicitly enabled AND a working compressor is
available, and never propagates an error.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from taskbrew.intelligence import compression


def _fake_result(content: str, *, before: int, after: int, transforms=None):
    """Build an object shaped like headroom's CompressResult."""
    return SimpleNamespace(
        messages=[{"role": "tool", "content": content}],
        tokens_before=before,
        tokens_after=after,
        tokens_saved=before - after,
        compression_ratio=(before - after) / before if before else 0.0,
        transforms_applied=transforms or ["smart_crusher"],
    )


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("TASKBREW_COMPRESSION", "1")
    monkeypatch.setenv("TASKBREW_COMPRESSION_MIN_CHARS", "0")
    yield


def test_disabled_by_default_is_passthrough(monkeypatch):
    monkeypatch.delenv("TASKBREW_COMPRESSION", raising=False)
    # Even if a compressor exists, disabled => untouched and never invoked.
    monkeypatch.setattr(
        compression, "_load_compress", lambda: pytest.fail("should not load when disabled")
    )
    text = "x" * 5000
    out, stats = compression.compress_text(text)
    assert out == text
    assert stats is None


def test_enabled_compresses(enabled, monkeypatch):
    def fake_compress(messages, **kwargs):
        assert messages[0]["role"] == "tool"
        assert kwargs["protect_recent"] == 0
        return _fake_result("SHORT", before=1000, after=200)

    monkeypatch.setattr(compression, "_load_compress", lambda: fake_compress)
    out, stats = compression.compress_text("original " * 500)
    assert out == "SHORT"
    assert stats is not None
    assert stats.tokens_saved == 800
    assert stats.tokens_before == 1000
    assert stats.tokens_after == 200
    assert stats.transforms == ["smart_crusher"]


def test_unavailable_dependency_is_passthrough(enabled, monkeypatch):
    monkeypatch.setattr(compression, "_load_compress", lambda: None)
    text = "y" * 5000
    out, stats = compression.compress_text(text)
    assert out == text
    assert stats is None


def test_compress_exception_is_passthrough(enabled, monkeypatch):
    def boom(messages, **kwargs):
        raise RuntimeError("headroom blew up")

    monkeypatch.setattr(compression, "_load_compress", lambda: boom)
    text = "z" * 5000
    out, stats = compression.compress_text(text)
    assert out == text
    assert stats is None


def test_no_savings_keeps_original(enabled, monkeypatch):
    # Compressor that returns a result with zero/negative savings.
    monkeypatch.setattr(
        compression,
        "_load_compress",
        lambda: (lambda messages, **kw: _fake_result("bigger", before=100, after=100)),
    )
    out, stats = compression.compress_text("a" * 5000)
    assert out == "a" * 5000
    assert stats is None


def test_below_min_chars_is_skipped(monkeypatch):
    monkeypatch.setenv("TASKBREW_COMPRESSION", "1")
    monkeypatch.setenv("TASKBREW_COMPRESSION_MIN_CHARS", "10000")
    monkeypatch.setattr(
        compression, "_load_compress", lambda: pytest.fail("should skip small blobs")
    )
    out, stats = compression.compress_text("small")
    assert out == "small"
    assert stats is None


def test_empty_text_is_passthrough(enabled):
    out, stats = compression.compress_text("")
    assert out == ""
    assert stats is None


def test_non_string_content_falls_back(enabled, monkeypatch):
    # If headroom returns list/multimodal content, keep the original string.
    bad = SimpleNamespace(
        messages=[{"role": "tool", "content": [{"type": "text", "text": "x"}]}],
        tokens_before=1000,
        tokens_after=200,
        tokens_saved=800,
        compression_ratio=0.8,
        transforms_applied=[],
    )
    monkeypatch.setattr(compression, "_load_compress", lambda: (lambda m, **k: bad))
    text = "keep me " * 500
    out, stats = compression.compress_text(text)
    assert out == text
    assert stats is None


@pytest.mark.asyncio
async def test_async_wrapper_disabled_skips_thread(monkeypatch):
    monkeypatch.delenv("TASKBREW_COMPRESSION", raising=False)
    out, stats = await compression.compress_text_async("hello")
    assert out == "hello"
    assert stats is None


@pytest.mark.asyncio
async def test_async_wrapper_compresses(enabled, monkeypatch):
    monkeypatch.setattr(
        compression,
        "_load_compress",
        lambda: (lambda m, **k: _fake_result("SMALL", before=900, after=100)),
    )
    out, stats = await compression.compress_text_async("big " * 500)
    assert out == "SMALL"
    assert stats is not None and stats.tokens_saved == 800


def test_is_enabled_reads_env(monkeypatch):
    for val in ("1", "true", "YES", "On"):
        monkeypatch.setenv("TASKBREW_COMPRESSION", val)
        assert compression.is_enabled() is True
    for val in ("0", "false", "", "nope"):
        monkeypatch.setenv("TASKBREW_COMPRESSION", val)
        assert compression.is_enabled() is False
