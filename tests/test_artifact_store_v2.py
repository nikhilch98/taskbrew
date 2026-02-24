"""Tests for the updated ArtifactStore (group_id/task_id layout)."""

from __future__ import annotations

import pytest

from ai_team.orchestrator.artifact_store import ArtifactStore


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def store(tmp_path) -> ArtifactStore:
    """Create an ArtifactStore backed by a temporary directory."""
    return ArtifactStore(str(tmp_path))


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


def test_save_and_load(store: ArtifactStore):
    """save_artifact followed by load_artifact should round-trip content."""
    path = store.save_artifact("GRP-001", "CD-001", "output.txt", "Hello, world!")

    assert path.endswith("output.txt")

    content = store.load_artifact("GRP-001", "CD-001", "output.txt")
    assert content == "Hello, world!"


def test_load_missing(store: ArtifactStore):
    """load_artifact for a nonexistent file should return an empty string."""
    content = store.load_artifact("GRP-999", "CD-999", "nope.txt")
    assert content == ""


def test_get_task_artifacts(store: ArtifactStore):
    """get_task_artifacts should list all filenames saved for a task."""
    store.save_artifact("GRP-001", "CD-001", "plan.md", "# Plan")
    store.save_artifact("GRP-001", "CD-001", "code.py", "print('hi')")

    files = store.get_task_artifacts("GRP-001", "CD-001")

    assert sorted(files) == ["code.py", "plan.md"]


def test_get_group_artifacts(store: ArtifactStore):
    """get_group_artifacts should return {task_id: [filenames]} for the group."""
    store.save_artifact("GRP-001", "CD-001", "output.txt", "result 1")
    store.save_artifact("GRP-001", "TS-001", "report.txt", "all passed")

    group_artifacts = store.get_group_artifacts("GRP-001")

    assert "CD-001" in group_artifacts
    assert "TS-001" in group_artifacts
    assert group_artifacts["CD-001"] == ["output.txt"]
    assert group_artifacts["TS-001"] == ["report.txt"]
