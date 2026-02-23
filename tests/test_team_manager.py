import pytest
from ai_team.orchestrator.team_manager import TeamManager
from ai_team.orchestrator.event_bus import EventBus
from ai_team.agents.base import AgentStatus


@pytest.fixture
def event_bus():
    return EventBus()

@pytest.fixture
def team_manager(event_bus):
    return TeamManager(event_bus=event_bus)

def test_spawn_agent(team_manager):
    team_manager.spawn_agent("coder")
    assert "coder" in team_manager.agents
    assert team_manager.agents["coder"].status == AgentStatus.IDLE

def test_spawn_duplicate_raises(team_manager):
    team_manager.spawn_agent("coder")
    with pytest.raises(ValueError, match="already exists"):
        team_manager.spawn_agent("coder")

def test_spawn_unknown_role_raises(team_manager):
    with pytest.raises(KeyError):
        team_manager.spawn_agent("nonexistent")

def test_stop_agent(team_manager):
    team_manager.spawn_agent("reviewer")
    team_manager.stop_agent("reviewer")
    assert team_manager.agents["reviewer"].status == AgentStatus.STOPPED

def test_get_team_status(team_manager):
    team_manager.spawn_agent("coder")
    team_manager.spawn_agent("reviewer")
    status = team_manager.get_team_status()
    assert len(status) == 2
    assert status["coder"] == AgentStatus.IDLE
    assert status["reviewer"] == AgentStatus.IDLE

def test_spawn_all_default_agents(team_manager):
    team_manager.spawn_default_team()
    assert len(team_manager.agents) == 6
    expected = {"pm", "researcher", "architect", "coder", "tester", "reviewer"}
    assert set(team_manager.agents.keys()) == expected
