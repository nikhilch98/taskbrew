"""Tests for Work Package persistence and task linkage."""

from __future__ import annotations

import subprocess
import sqlite3
from pathlib import Path

import pytest

from taskbrew.orchestrator.database import Database
from taskbrew.orchestrator.merge_queue import MergeQueue
from taskbrew.orchestrator.task_board import DEFAULT_MAX_REVIEW_ROUNDS, TaskBoard


class RecordingEventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
async def board(db: Database) -> TaskBoard:
    task_board = TaskBoard(
        db,
        group_prefixes={"pm": "FEAT", "architect": "DEBT"},
        event_bus=RecordingEventBus(),
    )
    await task_board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    return task_board


async def test_schema_has_work_package_tables(db: Database):
    tables = {
        row["name"]
        for row in await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert "milestones" in tables
    assert "work_packages" in tables
    assert "review_gates" in tables


async def test_tasks_schema_has_package_columns(db: Database):
    columns = {
        row["name"]
        for row in await db.execute_fetchall("PRAGMA table_info(tasks)")
    }

    assert {"work_package_id", "milestone_id", "review_scope"}.issubset(columns)


async def test_initialize_upgrades_legacy_db_before_package_indexes(tmp_path: Path):
    db_path = tmp_path / "legacy-v34.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                group_id TEXT,
                parent_id TEXT,
                title TEXT NOT NULL,
                assigned_to TEXT,
                claimed_by TEXT,
                status TEXT NOT NULL DEFAULT 'pending'
            );

            CREATE TABLE merge_queue (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                parent_task_id TEXT NOT NULL,
                verifier_task_id TEXT NOT NULL,
                source_branch TEXT NOT NULL,
                target_branch TEXT NOT NULL DEFAULT 'main',
                status TEXT NOT NULL DEFAULT 'queued',
                next_attempt_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );

            INSERT INTO schema_migrations (version, name, applied_at)
            VALUES (34, 'add_system_gate_task_fields', '2026-05-01T00:00:00+00:00');
            """
        )
        conn.commit()
    finally:
        conn.close()

    database = Database(str(db_path))
    await database.initialize()
    try:
        task_columns = {
            row["name"] for row in await database.execute_fetchall("PRAGMA table_info(tasks)")
        }
        queue_columns = {
            row["name"]
            for row in await database.execute_fetchall("PRAGMA table_info(merge_queue)")
        }
        indexes = {
            row["name"]
            for row in await database.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        version = await database.execute_fetchone(
            "SELECT MAX(version) AS version FROM schema_migrations"
        )
    finally:
        await database.close()

    assert {"work_package_id", "milestone_id", "review_scope"}.issubset(task_columns)
    assert {"source_type", "source_entity_id", "work_package_id"}.issubset(queue_columns)
    assert {
        "idx_tasks_work_package",
        "idx_merge_queue_source",
        "idx_merge_queue_package",
    }.issubset(indexes)
    assert version["version"] == 36


async def test_create_work_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    package = await board.create_work_package(
        group_id=group["id"],
        title="Implement dashboard live updates",
        description="Deliver websocket-driven board refresh.",
        created_by="architect-1",
        risk_level="medium",
    )

    assert package["id"].startswith("WP-")
    assert package["group_id"] == group["id"]
    assert package["title"] == "Implement dashboard live updates"
    assert package["status"] == "pending"
    assert package["review_scope"] == "work_package"
    assert package["review_status"] is None


async def test_create_task_assigns_default_work_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )

    assert task["work_package_id"].startswith("WP-")
    package = await board.get_work_package(task["work_package_id"])
    assert package["title"] == "Default package for Feature"
    assert package["group_id"] == group["id"]


async def test_create_task_can_update_package_metadata_from_design_output(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Default package for Feature",
        description="Automatically created to keep existing tasks grouped.",
        created_by="system",
    )

    task = await board.create_task(
        group_id=group["id"],
        title="Implement minimal addition CLI",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
        work_package_title="Minimal Addition CLI",
        work_package_description="Deliver a two-number addition script.",
    )

    updated_package = await board.get_work_package(task["work_package_id"])
    assert updated_package["title"] == "Minimal Addition CLI"
    assert updated_package["description"] == "Deliver a two-number addition script."


async def test_create_task_ignores_manual_package_for_default(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    manual_package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )

    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
    )

    assert task["work_package_id"] != manual_package["id"]
    package = await board.get_work_package(task["work_package_id"])
    assert package["title"] == "Default package for Feature"
    assert package["group_id"] == group["id"]
    assert package["created_by"] == "system"


async def test_package_status_tracks_child_task_progress(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")

    assert claimed is not None
    assert claimed["id"] == task["id"]
    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"

    await board.complete_task_with_output(task["id"], "Done.")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "review"
    assert package["review_status"] == "pending"


async def test_failed_child_blocks_package_review(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")

    assert claimed is not None
    assert claimed["id"] == task["id"]

    await board.fail_task(task["id"], reason="Tests failed")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "blocked"
    assert package["review_status"] is None


async def test_unblocked_child_reconciles_its_package(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package_a = await board.create_work_package(
        group_id=group["id"],
        title="Package A",
        created_by="architect-1",
    )
    package_b = await board.create_work_package(
        group_id=group["id"],
        title="Package B",
        created_by="architect-1",
    )
    task_a = await board.create_task(
        group_id=group["id"],
        title="Build foundation",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package_a["id"],
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Build dependent widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package_b["id"],
        blocked_by=[task_a["id"]],
    )

    await board.apply_backlog_intake_decision(
        task_a["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    await board.apply_backlog_intake_decision(
        task_b["id"],
        needs_review=False,
        reason="Covered by package review.",
    )

    package_b = await board.get_work_package(package_b["id"])
    assert package_b["status"] == "blocked"

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task_a["id"]

    await board.complete_task_with_output(task_a["id"], "Done.")

    task_b = await board.get_task(task_b["id"])
    package_b = await board.get_work_package(package_b["id"])
    assert task_b["status"] == "pending"
    assert package_b["status"] == "pending"


async def test_group_stays_active_while_package_review_pending(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task["id"]

    await board.complete_task_with_output(task["id"], "Done.")

    package = await board.get_work_package(package["id"])
    groups = await board.get_groups()
    completed_group = next(item for item in groups if item["id"] == group["id"])
    assert package["status"] == "review"
    assert package["review_status"] == "pending"
    assert completed_group["status"] == "active"
    assert completed_group["completed_at"] is None


async def test_in_progress_child_wins_over_pending_package_status(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task_a = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )
    task_b = await board.create_task(
        group_id=group["id"],
        title="Build another widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    for task in (task_a, task_b):
        await board.apply_backlog_intake_decision(
            task["id"],
            needs_review=False,
            reason="Covered by package review.",
        )

    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == task_a["id"]

    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"


async def test_ensure_review_gate_defaults_and_is_idempotent(board: TaskBoard):
    group = await board.create_group(title="Feature", created_by="pm")

    gate = await board.ensure_review_gate(
        entity_type="work_package",
        entity_id="WP-test",
        group_id=group["id"],
    )
    same_gate = await board.ensure_review_gate(
        entity_type="work_package",
        entity_id="WP-test",
        group_id=group["id"],
    )

    assert gate["id"] == same_gate["id"]
    assert gate["max_review_rounds"] == DEFAULT_MAX_REVIEW_ROUNDS


async def test_work_package_uses_configured_default_review_rounds(db: Database):
    board = TaskBoard(db, default_package_review_rounds=0)
    group = await board.create_group(title="Feature", created_by="pm")

    package = await board.create_work_package(
        group_id=group["id"],
        title="Unlimited review package",
        created_by="architect-1",
    )

    assert package["max_review_rounds"] == 0


async def test_reject_task_reconciles_package_before_group_completion(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    rejected = await board.reject_task(task["id"], "No longer needed.")

    package = await board.get_work_package(package["id"])
    groups = await board.get_groups()
    completed_group = next(item for item in groups if item["id"] == group["id"])
    assert rejected["status"] == "rejected"
    assert package["status"] == "rejected"
    assert completed_group["status"] == "completed"
    assert completed_group["completed_at"] is not None


async def test_completed_no_review_package_reopens_for_new_child_work(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Manual package",
        created_by="architect-1",
        review_scope="none",
    )
    first_task = await board.create_task(
        group_id=group["id"],
        title="Build widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        first_task["id"],
        needs_review=False,
        reason="No package review needed.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    assert claimed["id"] == first_task["id"]
    await board.complete_task_with_output(first_task["id"], "Done.")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "completed"
    assert package["review_status"] == "skipped"

    second_task = await board.create_task(
        group_id=group["id"],
        title="Build follow-up widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    package = await board.get_work_package(package["id"])
    assert second_task["status"] == "backlog"
    assert package["status"] == "pending"
    assert package["review_status"] is None

    await board.apply_backlog_intake_decision(
        second_task["id"],
        needs_review=False,
        reason="No package review needed.",
    )
    claimed = await board.claim_task("coder", "coder-2")

    assert claimed is not None
    assert claimed["id"] == second_task["id"]
    package = await board.get_work_package(package["id"])
    assert package["status"] == "in_progress"
    assert package["review_status"] is None


async def test_work_package_board_surfaces_review_gate_attention_metadata(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Reviewable package",
        created_by="architect-1",
        risk_level="high",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build reviewable widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(claimed["id"], "Done.")

    board_data = await board.get_work_package_board(group_id=group["id"])
    card = next(pkg for pkg in board_data["packages"] if pkg["id"] == package["id"])

    assert card["status"] == "review"
    assert card["task_counts"]["total"] == 1
    assert card["review_gate"]["entity_id"] == package["id"]
    assert card["review_gate"]["status"] == "pending"
    assert card["needs_attention"] is True
    assert any(reason["type"] == "review_gate" for reason in card["attention_reasons"])
    assert card["waiting_age_seconds"] >= 0
    assert "latest_review_reason_summary" in card


async def test_work_package_detail_includes_tasks_review_gate_and_timeline(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Detailed package",
        description="Package detail should expose review context.",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build detailed widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(claimed["id"], "Done.")

    detail = await board.get_work_package_detail(package["id"])

    assert detail is not None
    assert detail["id"] == package["id"]
    assert detail["tasks"][0]["id"] == task["id"]
    assert detail["review_gate"]["entity_id"] == package["id"]
    assert isinstance(detail["review_runs"], list)
    assert "artifacts" in detail
    assert any(item["type"] == "package.created" for item in detail["timeline"])


async def test_operations_summary_classifies_package_review_queue(
    board: TaskBoard,
):
    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Queued package",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build queued widget",
        task_type="implementation",
        assigned_to="coder",
        created_by="architect-1",
        work_package_id=package["id"],
    )

    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(claimed["id"], "Done.")

    summary = await board.get_operations_summary(group_id=group["id"])

    assert summary["counts"]["packages_total"] == 1
    assert summary["counts"]["packages_review"] == 1
    assert summary["counts"]["packages_attention"] == 1
    assert summary["system_agent"]["status"] == "idle"
    assert summary["queues"]["review"][0]["id"] == package["id"]
    assert summary["queues"]["attention"][0]["id"] == package["id"]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# Test\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "branch", "-M", "main")


async def test_package_review_approval_creates_final_integration_queue_item(
    db: Database,
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "add_two_numbers.py").write_text("print(1 + 2)\n")
    _git(repo, "add", "add_two_numbers.py")
    _git(repo, "commit", "-m", "add script")
    _git(repo, "checkout", "main")

    board = TaskBoard(db, group_prefixes={"pm": "FEAT"}, event_bus=RecordingEventBus())
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    queue = MergeQueue(db)
    board.configure_package_integration(merge_queue=queue, repo_dir=str(repo))

    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review bundle",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build package feature",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
        branch_name="feat/cd-001",
        parent_branch="main",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(task["id"], "Implemented package feature.")

    package_branch = f"package/{package['id'].lower()}"
    _git(repo, "checkout", "-b", package_branch)
    _git(repo, "merge", "--no-edit", "feat/cd-001")
    _git(repo, "checkout", "main")

    integration_task = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE work_package_id = ? AND task_type = 'package_integration'",
        (package["id"],),
    )
    assert integration_task is not None
    await board.apply_backlog_intake_decision(
        integration_task["id"],
        needs_review=False,
        reason="Package integration is reviewed at the package gate.",
    )
    claimed_integration = await board.claim_task("coder", "coder-2")
    assert claimed_integration is not None
    assert claimed_integration["id"] == integration_task["id"]
    await board.complete_task_with_output(
        integration_task["id"],
        "Merged task branches into the package branch.",
    )

    approved = await board.approve_work_package_review(
        package["id"],
        reason="Package satisfies spec.",
    )

    assert approved["status"] == "integrating"
    assert approved["review_status"] == "approved"
    rows = await db.execute_fetchall("SELECT * FROM merge_queue")
    assert len(rows) == 1
    assert rows[0]["group_id"] == group["id"]
    assert rows[0]["parent_task_id"] == integration_task["id"]
    assert rows[0]["verifier_task_id"] == integration_task["id"]
    assert rows[0]["source_type"] == "package_approval"
    assert rows[0]["source_entity_id"] == package["id"]
    assert rows[0]["work_package_id"] == package["id"]
    assert rows[0]["source_branch"] == package_branch
    assert rows[0]["target_branch"] == "main"

    board_data = await board.get_work_package_board(group_id=group["id"])
    card = next(pkg for pkg in board_data["packages"] if pkg["id"] == package["id"])
    assert card["integration_queue"][0]["id"] == rows[0]["id"]
    assert card["latest_integration"]["status"] == "queued"
    assert any(
        reason["type"] == "integration_pending"
        for reason in card["attention_reasons"]
    )

    detail = await board.get_work_package_detail(package["id"])
    assert detail["integration_queue"][0]["source_branch"] == package_branch

    await queue.complete(rows[0]["id"], status="merged")
    await board._check_group_completion(task["id"])
    still_waiting = await board.get_work_package(package["id"])
    assert still_waiting["status"] == "integrating"
    group_row = await db.execute_fetchone("SELECT * FROM groups WHERE id = ?", (group["id"],))
    assert group_row["status"] == "active"

    _git(repo, "merge", "--no-edit", package_branch)
    await board._check_group_completion(task["id"])

    completed_package = await board.get_work_package(package["id"])
    group_row = await db.execute_fetchone("SELECT * FROM groups WHERE id = ?", (group["id"],))
    assert completed_package["status"] == "completed"
    assert completed_package["completed_at"] is not None
    assert group_row["status"] == "completed"
    assert (repo / "add_two_numbers.py").exists()


async def test_package_integration_targets_main_for_revision_feature_branches(
    board: TaskBoard,
):
    assert board._package_target_branch({"parent_branch": "feat/cd-001"}) == "main"
    assert board._package_target_branch({"parent_branch": "bugfix/cd-001"}) == "main"
    assert board._package_target_branch({"parent_branch": "release/next"}) == "release/next"


async def test_package_waits_for_integration_task_before_review(
    db: Database,
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "feature.py").write_text("print('feature')\n")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "add feature")
    _git(repo, "checkout", "main")

    board = TaskBoard(db, group_prefixes={"pm": "FEAT"}, event_bus=RecordingEventBus())
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    queue = MergeQueue(db)
    board.configure_package_integration(merge_queue=queue, repo_dir=str(repo))

    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review bundle",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build package feature",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
        branch_name="feat/cd-001",
        parent_branch="main",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None

    await board.complete_task_with_output(task["id"], "Implemented package feature.")

    package = await board.get_work_package(package["id"])
    assert package["status"] == "integrating"
    gates = await db.execute_fetchall("SELECT * FROM review_gates")
    assert gates == []

    integration_tasks = await db.execute_fetchall(
        "SELECT * FROM tasks WHERE work_package_id = ? AND task_type = 'package_integration'",
        (package["id"],),
    )
    assert len(integration_tasks) == 1
    integration_task = integration_tasks[0]
    assert integration_task["assigned_to"] == "coder"
    assert integration_task["status"] == "backlog"
    assert integration_task["branch_name"] == f"package/{package['id'].lower()}"
    assert "feat/cd-001" in integration_task["description"]


async def test_completed_package_integration_task_opens_package_review(
    db: Database,
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "feature.py").write_text("print('feature')\n")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "add feature")
    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "package/wp-001")
    _git(repo, "merge", "--no-edit", "feat/cd-001")
    _git(repo, "checkout", "main")

    board = TaskBoard(db, group_prefixes={"pm": "FEAT"}, event_bus=RecordingEventBus())
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    queue = MergeQueue(db)
    board.configure_package_integration(merge_queue=queue, repo_dir=str(repo))

    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review bundle",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build package feature",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
        branch_name="feat/cd-001",
        parent_branch="main",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(task["id"], "Implemented package feature.")

    integration_task = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE work_package_id = ? AND task_type = 'package_integration'",
        (package["id"],),
    )
    assert integration_task is not None
    await board.apply_backlog_intake_decision(
        integration_task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed_integration = await board.claim_task("coder", "coder-2")
    assert claimed_integration is not None
    await board.complete_task_with_output(
        integration_task["id"],
        "Merged task branches into package branch.",
    )

    package = await board.get_work_package(package["id"])
    assert package["status"] == "review"
    assert package["review_status"] == "pending"
    gate = await db.execute_fetchone(
        "SELECT * FROM review_gates WHERE entity_type = 'work_package' AND entity_id = ?",
        (package["id"],),
    )
    assert gate is not None
    assert gate["status"] == "pending"


async def test_package_revision_completion_requires_fresh_package_integration(
    db: Database,
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "checkout", "-b", "feat/cd-001")
    (repo / "feature.py").write_text("print('feature')\n")
    _git(repo, "add", "feature.py")
    _git(repo, "commit", "-m", "add feature")
    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "package/wp-001")
    _git(repo, "merge", "--no-edit", "feat/cd-001")
    _git(repo, "checkout", "main")

    board = TaskBoard(db, group_prefixes={"pm": "FEAT"}, event_bus=RecordingEventBus())
    await board.register_prefixes({"pm": "PM", "architect": "AR", "coder": "CD"})
    queue = MergeQueue(db)
    board.configure_package_integration(merge_queue=queue, repo_dir=str(repo))

    group = await board.create_group(title="Feature", created_by="pm")
    package = await board.create_work_package(
        group_id=group["id"],
        title="Package review bundle",
        created_by="architect-1",
    )
    task = await board.create_task(
        group_id=group["id"],
        title="Build package feature",
        task_type="implementation",
        assigned_to="coder",
        work_package_id=package["id"],
        branch_name="feat/cd-001",
        parent_branch="main",
    )
    await board.apply_backlog_intake_decision(
        task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed = await board.claim_task("coder", "coder-1")
    assert claimed is not None
    await board.complete_task_with_output(task["id"], "Implemented package feature.")

    integration_task = await db.execute_fetchone(
        "SELECT * FROM tasks WHERE work_package_id = ? AND task_type = 'package_integration'",
        (package["id"],),
    )
    assert integration_task is not None
    await board.apply_backlog_intake_decision(
        integration_task["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed_integration = await board.claim_task("coder", "coder-2")
    assert claimed_integration is not None
    await board.complete_task_with_output(
        integration_task["id"],
        "Merged task branches into package branch.",
    )

    revisions = await board.create_package_revision_tasks(
        package["id"],
        [
            {
                "title": "Fix package test gap",
                "description": "Add the missing regression test.",
                "assigned_to": "coder",
                "task_type": "revision",
            }
        ],
        reason="Package review found a missing test.",
    )
    assert len(revisions) == 1
    await db.execute(
        "UPDATE review_gates SET status = 'waiting_revision', "
        "outcome = 'needs_revision', reason = ? "
        "WHERE entity_type = 'work_package' AND entity_id = ?",
        ("Package review found a missing test.", package["id"]),
    )
    revision = await board.get_task(revisions[0]["id"])
    assert revision is not None
    _git(repo, "checkout", "-b", revision["branch_name"])
    (repo / "test_feature.py").write_text("assert True\n")
    _git(repo, "add", "test_feature.py")
    _git(repo, "commit", "-m", "add package regression test")
    _git(repo, "checkout", "main")

    await board.apply_backlog_intake_decision(
        revision["id"],
        needs_review=False,
        reason="Covered by package review.",
    )
    claimed_revision = await board.claim_task("coder", "coder-3")
    assert claimed_revision is not None
    assert claimed_revision["id"] == revision["id"]
    await board.complete_task_with_output(revision["id"], "Added the regression test.")

    updated_package = await board.get_work_package(package["id"])
    assert updated_package["status"] == "integrating"
    integration_tasks = await db.execute_fetchall(
        "SELECT * FROM tasks WHERE work_package_id = ? AND task_type = 'package_integration' "
        "ORDER BY created_at",
        (package["id"],),
    )
    assert len(integration_tasks) == 2
    assert integration_tasks[-1]["status"] == "backlog"
    assert revision["branch_name"] in integration_tasks[-1]["description"]

    _git(repo, "checkout", "package/wp-001")
    _git(repo, "merge", "--no-edit", revision["branch_name"])
    _git(repo, "checkout", "main")

    await board.apply_backlog_intake_decision(
        integration_tasks[-1]["id"],
        needs_review=False,
        reason="Package integration is reviewed at the package gate.",
    )
    claimed_package_integration = await board.claim_task("coder", "coder-4")
    assert claimed_package_integration is not None
    assert claimed_package_integration["id"] == integration_tasks[-1]["id"]
    await board.complete_task_with_output(
        integration_tasks[-1]["id"],
        "Merged revision into package branch.",
    )

    ready_package = await board.get_work_package(package["id"])
    assert ready_package["status"] == "review"
    assert ready_package["review_status"] == "pending"
    ready_gate = await db.execute_fetchone(
        "SELECT * FROM review_gates WHERE entity_type = 'work_package' AND entity_id = ?",
        (package["id"],),
    )
    assert ready_gate["status"] == "pending"
    assert ready_gate["outcome"] is None
