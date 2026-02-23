"""Tests for task guardrails configuration."""

from taskbrew.config_loader import GuardrailsConfig, load_team_config


def test_guardrails_defaults():
    g = GuardrailsConfig()
    assert g.max_task_depth == 10
    assert g.max_tasks_per_group == 50
    assert g.rejection_cycle_limit == 3


def test_guardrails_custom_values():
    g = GuardrailsConfig(max_task_depth=5, max_tasks_per_group=20, rejection_cycle_limit=2)
    assert g.max_task_depth == 5
    assert g.max_tasks_per_group == 20
    assert g.rejection_cycle_limit == 2


def test_load_team_config_parses_guardrails(tmp_path):
    team_yaml = tmp_path / "team.yaml"
    team_yaml.write_text(
        'team_name: test\n'
        'database:\n  path: "~/.taskbrew/data/test.db"\n'
        'dashboard:\n  host: "0.0.0.0"\n  port: 8420\n'
        'artifacts:\n  base_dir: "artifacts"\n'
        'guardrails:\n'
        '  max_task_depth: 5\n'
        '  max_tasks_per_group: 20\n'
        '  rejection_cycle_limit: 2\n'
    )
    cfg = load_team_config(team_yaml)
    assert cfg.guardrails.max_task_depth == 5
    assert cfg.guardrails.max_tasks_per_group == 20
    assert cfg.guardrails.rejection_cycle_limit == 2


def test_load_team_config_guardrails_defaults(tmp_path):
    team_yaml = tmp_path / "team.yaml"
    team_yaml.write_text(
        'team_name: test\n'
        'database:\n  path: "~/.taskbrew/data/test.db"\n'
        'dashboard:\n  host: "0.0.0.0"\n  port: 8420\n'
        'artifacts:\n  base_dir: "artifacts"\n'
    )
    cfg = load_team_config(team_yaml)
    assert cfg.guardrails.max_task_depth == 10
    assert cfg.guardrails.max_tasks_per_group == 50
    assert cfg.guardrails.rejection_cycle_limit == 3
