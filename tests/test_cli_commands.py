"""Tests for CLI subcommands (init, doctor)."""

import argparse

import yaml

from taskbrew.main import _cmd_init, _cmd_doctor


def test_cmd_init_creates_structure(tmp_path):
    """taskbrew init should create config directories and files."""
    args = argparse.Namespace(name="test-project", dir=str(tmp_path), provider="claude")
    _cmd_init(args)

    assert (tmp_path / "config" / "team.yaml").exists()
    assert (tmp_path / "config" / "roles" / "pm.yaml").exists()
    assert (tmp_path / "config" / "providers").is_dir()
    assert (tmp_path / "plugins").is_dir()
    assert (tmp_path / ".env.example").exists()

    # Verify team.yaml contents reference the project name and provider
    team_content = (tmp_path / "config" / "team.yaml").read_text()
    assert "test-project" in team_content
    assert 'cli_provider: "claude"' in team_content
    assert "defaults:" in team_content

    # Verify PM role has routing fields
    pm_content = (tmp_path / "config" / "roles" / "pm.yaml").read_text()
    assert "routing_mode" in pm_content
    assert "can_create_groups" in pm_content
    assert "group_type" in pm_content

    # Verify .env.example exists with optional config
    env_content = (tmp_path / ".env.example").read_text()
    assert "TASKBREW_API_URL" in env_content


def test_cmd_init_gemini_provider(tmp_path):
    """taskbrew init --provider gemini should set cli_provider to gemini."""
    args = argparse.Namespace(name="gemini-proj", dir=str(tmp_path), provider="gemini")
    _cmd_init(args)

    team_content = (tmp_path / "config" / "team.yaml").read_text()
    assert 'cli_provider: "gemini"' in team_content

    env_content = (tmp_path / ".env.example").read_text()
    assert "TASKBREW_API_URL" in env_content


def test_cmd_init_doesnt_overwrite(tmp_path):
    """taskbrew init should not overwrite existing files."""
    # Create existing team.yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    team_yaml = config_dir / "team.yaml"
    team_yaml.write_text("existing: content")

    args = argparse.Namespace(name="test", dir=str(tmp_path), provider="claude")
    _cmd_init(args)

    assert team_yaml.read_text() == "existing: content"


def test_cmd_init_doesnt_overwrite_roles(tmp_path):
    """taskbrew init should not create pm.yaml if roles already exist."""
    roles_dir = tmp_path / "config" / "roles"
    roles_dir.mkdir(parents=True)
    existing_role = roles_dir / "custom.yaml"
    existing_role.write_text("role: custom")

    args = argparse.Namespace(name="test", dir=str(tmp_path), provider="claude")
    _cmd_init(args)

    # pm.yaml should NOT be created since a role already exists
    assert not (roles_dir / "pm.yaml").exists()
    assert existing_role.read_text() == "role: custom"


def test_cmd_init_default_name(tmp_path):
    """taskbrew init without --name should use directory name."""
    args = argparse.Namespace(name=None, dir=str(tmp_path), provider="claude")
    _cmd_init(args)

    team_content = (tmp_path / "config" / "team.yaml").read_text()
    assert tmp_path.name in team_content


def test_cmd_doctor_runs(capsys):
    """taskbrew doctor should run without errors."""
    args = argparse.Namespace()
    _cmd_doctor(args)

    captured = capsys.readouterr()
    assert "TaskBrew Doctor" in captured.out
    assert "Python" in captured.out
    assert "[OK]" in captured.out or "[FAIL]" in captured.out


def test_cmd_doctor_checks_python_version(capsys):
    """taskbrew doctor should report Python version."""
    import sys

    args = argparse.Namespace()
    _cmd_doctor(args)

    captured = capsys.readouterr()
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    assert py_version in captured.out


def test_cmd_init_generates_valid_config(tmp_path):
    """Generated config should parse without errors."""
    from taskbrew.config_loader import load_team_config, _parse_role

    args = argparse.Namespace(name="test-project", dir=str(tmp_path), provider="claude")
    _cmd_init(args)

    # team.yaml should parse
    tc = load_team_config(tmp_path / "config" / "team.yaml")
    assert tc.team_name == "test-project"

    # PM role should parse
    with open(tmp_path / "config" / "roles" / "pm.yaml") as f:
        role_data = yaml.safe_load(f)
    role = _parse_role(role_data)
    assert role.routing_mode == "open"
    assert role.can_create_groups is True
