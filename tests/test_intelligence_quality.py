"""Tests for the QualityManager."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.intelligence.quality import QualityManager


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def db():
    """Create and initialise an in-memory database."""
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def quality(db: Database) -> QualityManager:
    """Create a QualityManager backed by the in-memory database."""
    return QualityManager(db)


async def _create_task(db: Database, task_type: str = "implementation") -> str:
    """Insert a minimal task + group so foreign key constraints are satisfied."""
    now = datetime.now(timezone.utc).isoformat()
    group_id = f"GRP-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO groups (id, title, origin, status, created_at) VALUES (?, 'Test', 'test', 'active', ?)",
        (group_id, now),
    )
    task_id = f"TSK-{uuid.uuid4().hex[:4]}"
    await db.execute(
        "INSERT INTO tasks (id, group_id, title, task_type, priority, assigned_to, status, created_by, created_at) "
        "VALUES (?, ?, 'Test Task', ?, 'medium', 'coder', 'completed', 'test', ?)",
        (task_id, group_id, task_type, now),
    )
    return task_id


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


async def test_record_score(quality: QualityManager, db: Database):
    """Store and retrieve a quality score."""
    task_id = await _create_task(db)

    result = await quality.record_score(task_id, "coder", "self_review", 0.85, {"key": "value"})

    assert result["task_id"] == task_id
    assert result["agent_id"] == "coder"
    assert result["score_type"] == "self_review"
    assert result["score"] == 0.85

    # Verify persisted
    scores = await quality.get_scores(task_id=task_id)
    assert len(scores) == 1
    assert scores[0]["score"] == 0.85
    assert scores[0]["details"] == {"key": "value"}


async def test_extract_self_review_high_quality(quality: QualityManager, db: Database):
    """Output with testing + error handling + review signals scores high."""
    task_id = await _create_task(db)

    output = (
        "I implemented the feature with comprehensive pytest tests. "
        "Added try/except error handling throughout. "
        "Included docstring documentation for all functions. "
        "Please review and verify the changes.\n"
        "```python\ndef example(): pass\n```"
    )

    result = await quality.extract_self_review(task_id, "coder", output)

    assert result["score"] >= 0.9
    assert result["signals"]["mentions_testing"] is True
    assert result["signals"]["mentions_error_handling"] is True
    assert result["signals"]["mentions_documentation"] is True
    assert result["signals"]["mentions_review"] is True
    assert result["signals"]["has_code_blocks"] is True


async def test_extract_self_review_low_quality(quality: QualityManager, db: Database):
    """Minimal output with no quality signals scores at baseline."""
    task_id = await _create_task(db)

    output = "Done. Made some changes to the file."

    result = await quality.extract_self_review(task_id, "coder", output)

    assert result["score"] == 0.5  # baseline only
    assert result["signals"]["mentions_testing"] is False
    assert result["signals"]["mentions_error_handling"] is False
    assert result["signals"]["mentions_documentation"] is False
    assert result["signals"]["mentions_review"] is False
    assert result["signals"]["has_code_blocks"] is False


async def test_score_confidence_high(quality: QualityManager, db: Database):
    """Output with high-confidence phrases scores above baseline."""
    task_id = await _create_task(db)

    output = "All tests pass. The implementation has been verified and confirmed to work correctly."

    confidence = await quality.score_confidence(task_id, "coder", output)

    # "all tests pass", "verified", "confirmed", "correct" = 4 high phrases -> 0.7 + 0.2 = 0.9
    assert confidence >= 0.85


async def test_score_confidence_low(quality: QualityManager, db: Database):
    """Output with low-confidence phrases scores below baseline."""
    task_id = await _create_task(db)

    output = "I'm not sure this is right. Maybe it could be improved. I think it might work, perhaps."

    confidence = await quality.score_confidence(task_id, "coder", output)

    # "i'm not sure", "maybe", "could be", "i think", "might", "perhaps" = 6 low phrases
    # 0.7 - 0.3 = 0.4
    assert confidence <= 0.5


async def test_score_code_quality(quality: QualityManager, db: Database):
    """Code output with functions, type hints, docstrings scores well."""
    task_id = await _create_task(db)

    output = '''import json
from datetime import datetime

class DataProcessor:
    """Process incoming data."""

    def process(self, data: str) -> dict:
        """Parse and validate data."""
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return {}
'''

    result = await quality.score_code_quality(task_id, "coder", output)

    assert result["checks"]["has_imports"] is True
    assert result["checks"]["has_functions"] is True
    assert result["checks"]["has_classes"] is True
    assert result["checks"]["has_type_hints"] is True
    assert result["checks"]["has_error_handling"] is True
    assert result["checks"]["has_docstrings"] is True
    assert result["passed"] >= 6
    assert result["score"] >= 0.8


async def test_should_iterate_below_threshold(quality: QualityManager, db: Database):
    """Returns True when the latest score is below the threshold."""
    task_id = await _create_task(db)

    await quality.record_score(task_id, "coder", "self_review", 0.4)

    assert await quality.should_iterate(task_id, threshold=0.6) is True


async def test_should_iterate_above_threshold(quality: QualityManager, db: Database):
    """Returns False when the latest score is above the threshold."""
    task_id = await _create_task(db)

    await quality.record_score(task_id, "coder", "self_review", 0.85)

    assert await quality.should_iterate(task_id, threshold=0.6) is False


async def test_should_iterate_no_scores(quality: QualityManager, db: Database):
    """Returns False when there are no scores at all."""
    task_id = await _create_task(db)

    assert await quality.should_iterate(task_id) is False


async def test_check_regression(quality: QualityManager, db: Database):
    """Detects regression when current score drops significantly below average."""
    # Create several tasks with good scores to build a history
    for _ in range(5):
        tid = await _create_task(db, task_type="implementation")
        await quality.record_score(tid, "coder", "self_review", 0.85)

    # Create a task with a low score (regression)
    bad_task = await _create_task(db, task_type="implementation")
    await quality.record_score(bad_task, "coder", "self_review", 0.4)

    result = await quality.check_regression(bad_task)

    assert result["task_type"] == "implementation"
    assert result["current_score"] == 0.4
    assert result["regression_detected"] is True
    assert result["regression_delta"] < 0


async def test_get_task_quality_summary(quality: QualityManager, db: Database):
    """Summary aggregates all score types for a task."""
    task_id = await _create_task(db)

    await quality.record_score(task_id, "coder", "self_review", 0.8, {"key": "a"})
    await quality.record_score(task_id, "coder", "confidence", 0.9, {"key": "b"})
    await quality.record_score(task_id, "coder", "code_quality", 0.75, {"key": "c"})

    summary = await quality.get_task_quality_summary(task_id)

    assert summary["task_id"] == task_id
    assert "self_review" in summary["scores"]
    assert "confidence" in summary["scores"]
    assert "code_quality" in summary["scores"]
    assert summary["scores"]["self_review"]["score"] == 0.8
    assert summary["scores"]["confidence"]["score"] == 0.9
    assert summary["scores"]["code_quality"]["score"] == 0.75


async def test_no_print_debugging_detects_print_statements(quality: QualityManager, db: Database):
    """Regression: no_print_debugging must be False when print( is present.

    Previously used `or` instead of `and`, which made the check always pass
    because if `print(` was found, `print(f` might not be (making `or` true).
    """
    task_id = await _create_task(db)

    output_with_print = '''import json

def process(data: str) -> dict:
    """Process data."""
    print("debugging value:", data)
    try:
        return json.loads(data)
    except Exception:
        return {}
'''

    result = await quality.score_code_quality(task_id, "coder", output_with_print)
    assert result["checks"]["no_print_debugging"] is False


async def test_no_print_debugging_detects_fstring_print(quality: QualityManager, db: Database):
    """Regression: no_print_debugging must be False when print(f is present."""
    task_id = await _create_task(db)

    output_with_fprint = '''import json

def process(data: str) -> dict:
    """Process data."""
    print(f"value = {data}")
    try:
        return json.loads(data)
    except Exception:
        return {}
'''

    result = await quality.score_code_quality(task_id, "coder", output_with_fprint)
    assert result["checks"]["no_print_debugging"] is False


async def test_no_print_debugging_passes_without_prints(quality: QualityManager, db: Database):
    """no_print_debugging should be True when no print statements are present."""
    task_id = await _create_task(db)

    clean_output = '''import json

def process(data: str) -> dict:
    """Process data."""
    try:
        return json.loads(data)
    except Exception:
        return {}
'''

    result = await quality.score_code_quality(task_id, "coder", clean_output)
    assert result["checks"]["no_print_debugging"] is True
