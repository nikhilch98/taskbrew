import pytest
import os
from ai_team.orchestrator.artifact_store import ArtifactStore


@pytest.fixture
def artifact_store(tmp_path):
    return ArtifactStore(base_dir=str(tmp_path / "artifacts"))


def test_get_artifact_dir_creates_directory(artifact_store):
    path = artifact_store.get_artifact_dir("run-1", 0, "researcher")
    assert os.path.isdir(path)
    assert "run-1" in path
    assert "0_researcher" in path


def test_save_and_load_artifact(artifact_store):
    artifact_store.save_artifact("run-1", 0, "researcher", "research.md", "# Research\nFindings here")
    content = artifact_store.load_artifact("run-1", 0, "researcher", "research.md")
    assert "Findings here" in content


def test_get_step_artifacts_lists_all_files(artifact_store):
    artifact_store.save_artifact("run-1", 0, "researcher", "research.md", "content1")
    artifact_store.save_artifact("run-1", 0, "researcher", "notes.txt", "content2")
    files = artifact_store.get_step_artifacts("run-1", 0, "researcher")
    assert len(files) == 2
    assert "research.md" in files
    assert "notes.txt" in files


def test_get_previous_artifacts_for_step(artifact_store):
    artifact_store.save_artifact("run-1", 0, "researcher", "research.md", "research content")
    artifact_store.save_artifact("run-1", 1, "architect", "design.md", "design content")
    prev = artifact_store.get_previous_artifacts("run-1", step_index=2)
    assert len(prev) == 2
    assert prev[0]["agent"] == "researcher"
    assert prev[1]["agent"] == "architect"


def test_build_context_from_artifacts(artifact_store):
    artifact_store.save_artifact("run-1", 0, "researcher", "research.md", "# Research\nKey findings")
    context = artifact_store.build_context("run-1", step_index=1)
    assert "Research" in context
    assert "Key findings" in context
    assert "Step 0" in context


def test_get_previous_artifacts_empty_for_first_step(artifact_store):
    prev = artifact_store.get_previous_artifacts("run-1", step_index=0)
    assert prev == []


def test_build_context_empty_for_first_step(artifact_store):
    context = artifact_store.build_context("run-1", step_index=0)
    assert context == ""
