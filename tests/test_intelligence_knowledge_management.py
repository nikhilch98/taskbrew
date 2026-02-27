"""Tests for the KnowledgeManager (features 45-48)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.knowledge_management import KnowledgeManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def manager(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = KnowledgeManager(db, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    yield mgr
    await db.close()


@pytest.fixture
async def db_and_manager(tmp_path):
    """Expose both db and manager when raw DB access is needed."""
    db = Database(str(tmp_path / "test.db"))
    await db.initialize()
    mgr = KnowledgeManager(db, project_dir=str(tmp_path))
    await mgr.ensure_tables()
    yield db, mgr
    await db.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _past_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ------------------------------------------------------------------
# Tests: Feature 45 - Knowledge Decay Tracker
# ------------------------------------------------------------------


async def test_track_knowledge_basic(manager: KnowledgeManager):
    """track_knowledge stores an entry and returns it."""
    result = await manager.track_knowledge(
        "api-auth-flow", "OAuth2 with PKCE", source_file="auth.py", source_agent="architect"
    )
    assert result["id"].startswith("KE-")
    assert result["key"] == "api-auth-flow"
    assert result["content"] == "OAuth2 with PKCE"
    assert result["source_file"] == "auth.py"
    assert result["source_agent"] == "architect"
    assert result["created_at"] == result["updated_at"]


async def test_check_staleness_flags_old_entries(db_and_manager):
    """check_staleness flags entries older than max_age_days."""
    db, mgr = db_and_manager
    # Insert an entry with an old timestamp directly
    old_time = _past_iso(45)
    await db.execute(
        "INSERT INTO knowledge_entries (id, key, content, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("KE-old1", "old-key", "old content", old_time, old_time),
    )

    flagged = await mgr.check_staleness(max_age_days=30)
    assert len(flagged) == 1
    assert flagged[0]["entry_id"] == "KE-old1"
    assert "30 days" in flagged[0]["reason"]


async def test_refresh_knowledge_updates_timestamp(db_and_manager):
    """refresh_knowledge updates the entry and resolves staleness flags."""
    db, mgr = db_and_manager
    old_time = _past_iso(60)
    await db.execute(
        "INSERT INTO knowledge_entries (id, key, content, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("KE-refresh", "stale-key", "old info", old_time, old_time),
    )
    # Flag it
    await mgr.check_staleness(max_age_days=30)
    stale = await mgr.get_stale_entries()
    assert len(stale) == 1

    # Refresh
    updated = await mgr.refresh_knowledge("KE-refresh", new_content="new info")
    assert updated is not None
    assert updated["content"] == "new info"
    assert updated["updated_at"] > old_time

    # Staleness flags should be resolved
    stale_after = await mgr.get_stale_entries()
    assert len(stale_after) == 0


async def test_get_stale_entries_empty(manager: KnowledgeManager):
    """get_stale_entries returns empty when nothing is stale."""
    await manager.track_knowledge("fresh", "just created")
    stale = await manager.get_stale_entries()
    assert stale == []


# ------------------------------------------------------------------
# Tests: Feature 46 - Documentation Gap Detector
# ------------------------------------------------------------------


async def test_scan_for_gaps_finds_undocumented(manager: KnowledgeManager, tmp_path):
    """scan_for_gaps detects classes and functions not mentioned in docs."""
    code = tmp_path / "module.py"
    code.write_text("class UserService:\n    pass\n\ndef compute_total():\n    pass\n")
    doc = tmp_path / "docs.md"
    doc.write_text("# API\nUserService is documented here.\n")

    gaps = await manager.scan_for_gaps([str(code)], [str(doc)])
    assert len(gaps) == 1
    assert gaps[0]["symbol_name"] == "compute_total"
    assert gaps[0]["symbol_type"] == "function"
    assert gaps[0]["severity"] == "medium"


async def test_get_gaps_filtered(manager: KnowledgeManager, tmp_path):
    """get_gaps filters by severity."""
    code = tmp_path / "models.py"
    code.write_text("class Order:\n    pass\n\ndef helper():\n    pass\n")
    doc = tmp_path / "empty.md"
    doc.write_text("")

    await manager.scan_for_gaps([str(code)], [str(doc)])

    high_gaps = await manager.get_gaps(severity="high")
    assert all(g["severity"] == "high" for g in high_gaps)
    assert any(g["symbol_name"] == "Order" for g in high_gaps)

    medium_gaps = await manager.get_gaps(severity="medium")
    assert all(g["severity"] == "medium" for g in medium_gaps)


async def test_resolve_gap_and_coverage(manager: KnowledgeManager, tmp_path):
    """resolve_gap marks a gap as resolved and coverage_stats reflect it."""
    code = tmp_path / "svc.py"
    code.write_text("class PaymentService:\n    pass\n\ndef validate():\n    pass\n")
    doc = tmp_path / "empty.md"
    doc.write_text("")

    gaps = await manager.scan_for_gaps([str(code)], [str(doc)])
    assert len(gaps) == 2

    stats_before = await manager.get_coverage_stats()
    assert stats_before["undocumented_symbols"] == 2
    assert stats_before["coverage_percent"] == 0.0

    # Resolve one
    resolved = await manager.resolve_gap(gaps[0]["id"], doc_reference="docs.md#payment")
    assert resolved is not None
    assert resolved["resolved"] == 1

    stats_after = await manager.get_coverage_stats()
    assert stats_after["documented_symbols"] == 1
    assert stats_after["coverage_percent"] == 50.0


# ------------------------------------------------------------------
# Tests: Feature 47 - Institutional Knowledge Extractor
# ------------------------------------------------------------------


async def test_extract_from_commit_with_decision_keyword(manager: KnowledgeManager):
    """extract_from_commit captures commits with decision keywords."""
    result = await manager.extract_from_commit(
        "abc123",
        "Switched to PostgreSQL because SQLite cannot handle concurrent writes",
        "alice",
        ["db.py", "config.py"],
    )
    assert result is not None
    assert result["source_type"] == "commit"
    assert result["source_ref"] == "abc123"
    assert "because" in result["tags"]


async def test_extract_from_commit_no_match(manager: KnowledgeManager):
    """extract_from_commit returns None for normal commits."""
    result = await manager.extract_from_commit(
        "def456", "fix typo in readme", "bob", ["README.md"]
    )
    assert result is None


async def test_extract_from_comment_with_tags(manager: KnowledgeManager):
    """extract_from_comment captures TODO/HACK/NOTE comments."""
    result = await manager.extract_from_comment(
        "utils.py", 42, "# HACK: workaround for upstream bug in requests library"
    )
    assert result is not None
    assert result["source_type"] == "comment"
    assert result["file_path"] == "utils.py"
    assert result["line_number"] == 42
    tags = json.loads(result["tags"])
    assert "HACK" in tags


async def test_search_knowledge(manager: KnowledgeManager):
    """search_knowledge finds entries by keyword."""
    await manager.extract_from_commit(
        "aaa", "Workaround for rate limiting because API throttles", "alice", ["api.py"]
    )
    await manager.extract_from_comment(
        "cache.py", 10, "# NOTE: cache invalidation is tricky"
    )

    results = await manager.search_knowledge("rate limiting")
    assert len(results) >= 1
    assert any("rate limiting" in r["content"] for r in results)


# ------------------------------------------------------------------
# Tests: Feature 48 - Context Compression Engine
# ------------------------------------------------------------------


async def test_compress_context_fits_budget(manager: KnowledgeManager):
    """compress_context keeps high-salience items within token budget."""
    items = [
        {"id": "a", "tokens": 100, "recency": 0.9, "relevance": 0.8, "frequency": 0.5},
        {"id": "b", "tokens": 100, "recency": 0.2, "relevance": 0.3, "frequency": 0.1},
        {"id": "c", "tokens": 100, "recency": 0.7, "relevance": 0.9, "frequency": 0.8},
    ]
    result = await manager.compress_context(items, max_tokens=200)

    assert result["total_tokens"] == 200
    assert len(result["kept"]) == 2
    assert len(result["dropped"]) == 1
    # Dropped item should be the lowest scoring one (b)
    assert result["dropped"][0]["id"] == "b"


async def test_compress_context_all_fit(manager: KnowledgeManager):
    """compress_context keeps everything when budget is large enough."""
    items = [
        {"id": "x", "tokens": 50, "recency": 0.5, "relevance": 0.5, "frequency": 0.5},
        {"id": "y", "tokens": 50, "recency": 0.5, "relevance": 0.5, "frequency": 0.5},
    ]
    result = await manager.compress_context(items, max_tokens=1000)
    assert len(result["kept"]) == 2
    assert len(result["dropped"]) == 0
    assert result["total_tokens"] == 100


async def test_record_and_get_compression_stats(manager: KnowledgeManager):
    """record_compression and get_compression_stats work end-to-end."""
    await manager.record_compression("task-1", 1000, 400, 5, 3)
    await manager.record_compression("task-2", 2000, 600, 8, 12)

    stats = await manager.get_compression_stats()
    assert stats["total_compressions"] == 2
    assert stats["total_items_kept"] == 13
    assert stats["total_items_dropped"] == 15
    assert 0 < stats["avg_compression_ratio"] < 1


async def test_set_salience_weights(manager: KnowledgeManager):
    """set_salience_weights changes scoring behaviour."""
    # Set relevance to dominate
    await manager.set_salience_weights(
        recency_weight=0.0, relevance_weight=1.0, frequency_weight=0.0
    )
    items = [
        {"id": "high-relevance", "tokens": 100, "recency": 0.1, "relevance": 1.0, "frequency": 0.1},
        {"id": "high-recency", "tokens": 100, "recency": 1.0, "relevance": 0.1, "frequency": 0.1},
    ]
    result = await manager.compress_context(items, max_tokens=100)
    assert len(result["kept"]) == 1
    assert result["kept"][0]["id"] == "high-relevance"
