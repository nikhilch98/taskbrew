"""Tests for the updated ArtifactStore (group_id/task_id layout)."""

from __future__ import annotations

import pytest

from taskbrew.orchestrator.artifact_store import ArtifactStore


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


def test_load_artifact_truncates_oversized_file(store: ArtifactStore, tmp_path):
    """A multi-MB artifact must be returned with a truncation marker, not
    loaded entirely into memory."""
    from taskbrew.orchestrator import artifact_store as _mod
    # Save a file directly (bypassing the API path) at well over the cap.
    target_dir = tmp_path / "GRP-001" / "CD-001"
    target_dir.mkdir(parents=True)
    huge = target_dir / "big.log"
    huge.write_bytes(b"x" * (_mod.MAX_LOAD_ARTIFACT_BYTES + 100))

    content = store.load_artifact("GRP-001", "CD-001", "big.log")
    # Truncated: first MAX bytes plus a marker line.
    assert len(content) <= _mod.MAX_LOAD_ARTIFACT_BYTES + 256
    assert "truncated by TaskBrew" in content


def test_ingest_file_copies_into_store(store: ArtifactStore, tmp_path):
    """ingest_file copies an external file under group/task/<basename>."""
    src = tmp_path / "impl_summary.md"
    src.write_text("# Summary\nDid the thing.\n")

    dest = store.ingest_file("GRP-001", "CD-001", str(src))
    assert dest is not None
    assert dest.endswith("/CD-001/impl_summary.md")

    files = store.get_task_artifacts("GRP-001", "CD-001")
    assert files == ["impl_summary.md"]
    assert store.load_artifact("GRP-001", "CD-001", "impl_summary.md").startswith("# Summary")


def test_ingest_file_returns_none_for_missing_source(store: ArtifactStore, tmp_path):
    dest = store.ingest_file(
        "GRP-001", "CD-001", str(tmp_path / "does-not-exist.md"),
    )
    assert dest is None


def test_ingest_file_truncates_oversized_source(store: ArtifactStore, tmp_path):
    """Oversized source files are copied with a truncation marker rather
    than rejected (so the user still sees something) but the dashboard
    process never reads more than max_bytes."""
    src = tmp_path / "big.log"
    src.write_bytes(b"x" * (5_000_000))  # 5 MB

    dest = store.ingest_file(
        "GRP-001", "CD-001", str(src), max_bytes=1_000_000,
    )
    assert dest is not None
    content = store.load_artifact("GRP-001", "CD-001", "big.log")
    assert "truncated by TaskBrew" in content
