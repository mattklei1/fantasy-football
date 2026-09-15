"""Unit tests for fantasy_football.league_registry - real filesystem I/O
against a temp path (monkeypatched REGISTRY_PATH), no network/DB. Commit
behavior (_commit_registry) is exercised via config.github_token()
returning None (no token configured in the test env by default) so it
degrades to the "local only" message rather than hitting the real
GitHub API - same convention as test_rank_overrides.py."""
from fantasy_football import league_registry


def test_load_registered_leagues_empty_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(league_registry, "REGISTRY_PATH", tmp_path / "leagues.json")
    assert league_registry.load_registered_leagues() == {}


def test_add_league_then_load_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(league_registry, "REGISTRY_PATH", tmp_path / "leagues.json")
    monkeypatch.setattr("fantasy_football.config.github_token", lambda: None)

    league_registry.add_league(12345, "The Dynasty", 2026)
    leagues = league_registry.load_registered_leagues()
    assert leagues == {12345: {"name": "The Dynasty", "season": 2026}}


def test_add_league_reports_not_committed_without_token(monkeypatch, tmp_path):
    monkeypatch.setattr(league_registry, "REGISTRY_PATH", tmp_path / "leagues.json")
    monkeypatch.setattr("fantasy_football.config.github_token", lambda: None)

    result = league_registry.add_league(12345, "The Dynasty", 2026)
    assert result["committed"] is False
    assert "GITHUB_TOKEN" in result["commit_message"]


def test_remove_league_deletes_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(league_registry, "REGISTRY_PATH", tmp_path / "leagues.json")
    monkeypatch.setattr("fantasy_football.config.github_token", lambda: None)

    league_registry.add_league(1, "A", 2026)
    league_registry.add_league(2, "B", 2026)
    league_registry.remove_league(1)

    assert league_registry.load_registered_leagues() == {2: {"name": "B", "season": 2026}}


def test_remove_nonexistent_league_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(league_registry, "REGISTRY_PATH", tmp_path / "leagues.json")
    monkeypatch.setattr("fantasy_football.config.github_token", lambda: None)

    league_registry.add_league(1, "A", 2026)
    league_registry.remove_league(999)
    assert league_registry.load_registered_leagues() == {1: {"name": "A", "season": 2026}}


def test_load_registered_leagues_ignores_malformed_json(monkeypatch, tmp_path):
    path = tmp_path / "leagues.json"
    path.write_text("not valid json {")
    monkeypatch.setattr(league_registry, "REGISTRY_PATH", path)
    assert league_registry.load_registered_leagues() == {}
