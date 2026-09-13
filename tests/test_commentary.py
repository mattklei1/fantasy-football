"""Unit tests for fantasy_football.commentary - builds a minimal
in-memory SQLite DB via db.init_db() rather than mocking SQL, so these
exercise the real queries. No live Claude/ESPN calls (per PROJECT_BRIEF
testing requirements) - the Claude path is exercised with a monkeypatched
generate_claude_commentary, never the real API."""
import sqlite3

import pytest

from fantasy_football import commentary, db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(c)
    c.execute(
        "INSERT INTO seasons (season_id, league_id, reg_season_count, playoff_team_count, position_slot_counts) "
        "VALUES (2099, 1, 14, 6, '{\"QB\": 1, \"BE\": 5}')"
    )
    c.execute("INSERT INTO managers (manager_id, display_name) VALUES ('m1', 'Alice')")
    c.execute("INSERT INTO managers (manager_id, display_name) VALUES ('m2', 'Bob')")
    c.execute("INSERT INTO managers (manager_id, display_name) VALUES ('m3', 'Cara')")
    c.execute("INSERT INTO managers (manager_id, display_name) VALUES ('m4', 'Dan')")
    for i, (mgr, name) in enumerate([("m1", "Team A"), ("m2", "Team B"), ("m3", "Team C"), ("m4", "Team D")], start=1):
        c.execute(
            "INSERT INTO teams (id, season_id, espn_team_id, team_name) VALUES (?, 2099, ?, ?)", (i, i, name)
        )
        c.execute("INSERT INTO team_owners (team_pk, manager_id) VALUES (?, ?)", (i, mgr))

    # week 1: team1 beats team2 (150-100), team3 beats team4 (120-110)
    for team_pk, week, score in [(1, 1, 150.0), (2, 1, 100.0), (3, 1, 120.0), (4, 1, 110.0)]:
        c.execute(
            "INSERT INTO weekly_team_scores (season_id, week, team_pk, score, completed, is_playoff) "
            "VALUES (2099, ?, ?, ?, 1, 0)", (week, team_pk, score),
        )
    c.execute(
        "INSERT INTO matchups (season_id, week, home_team_pk, away_team_pk, home_score, away_score, "
        "matchup_type, completed) VALUES (2099, 1, 1, 2, 150.0, 100.0, 'NONE', 1)"
    )
    c.execute(
        "INSERT INTO matchups (season_id, week, home_team_pk, away_team_pk, home_score, away_score, "
        "matchup_type, completed) VALUES (2099, 1, 3, 4, 120.0, 110.0, 'NONE', 1)"
    )
    # future week 2 schedule (not yet played) - for next_week_game_to_watch
    c.execute(
        "INSERT INTO matchups (season_id, week, home_team_pk, away_team_pk, matchup_type, completed) "
        "VALUES (2099, 2, 1, 3, 'NONE', 0)"
    )
    c.execute(
        "INSERT INTO matchups (season_id, week, home_team_pk, away_team_pk, matchup_type, completed) "
        "VALUES (2099, 2, 2, 4, 'NONE', 0)"
    )
    # metrics_weekly week 1 snapshot (power_score drives movers/game-to-watch)
    for team_pk, power, ppg in [(1, 90.0, 150.0), (2, 40.0, 100.0), (3, 70.0, 120.0), (4, 60.0, 110.0)]:
        c.execute(
            "INSERT INTO metrics_weekly (season_id, week, team_pk, power_score, ppg, last3_ppg, fraud_index) "
            "VALUES (2099, 1, ?, ?, ?, ?, 0.1)", (team_pk, power, ppg, ppg),
        )
    c.commit()
    return c


def test_build_weekly_facts_returns_none_for_incomplete_week(conn):
    assert commentary.build_weekly_facts(conn, 2099, 2) is None


def test_build_weekly_facts_returns_populated_facts(conn):
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    assert facts is not None
    assert facts["season"] == 2099
    assert facts["week"] == 1
    assert facts["awards"]["highest_score"]["team"]["team_name"] == "Team A"
    assert facts["awards"]["highest_score"]["team"]["manager_name"] == "Alice"
    # no metrics_weekly row for week 0 -> movers list is empty, not a crash
    assert facts["next_week_game_to_watch"] is not None


def test_placeholder_commentary_includes_every_section(conn):
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    text = commentary.generate_placeholder_commentary(facts)
    for header in commentary.SECTION_ORDER:
        assert header in text


def test_get_or_generate_recap_uses_placeholder_without_api_key(conn, monkeypatch):
    monkeypatch.setattr(commentary.config, "anthropic_api_key", lambda: None)
    result = commentary.get_or_generate_weekly_recap(conn, 2099, 1)
    assert result["source"] == "placeholder"
    assert "HEADLINE" in result["commentary"]

    row = conn.execute(
        "SELECT COUNT(*) FROM weekly_recaps WHERE season_id = 2099 AND week = 1"
    ).fetchone()[0]
    assert row == 1


def test_get_or_generate_recap_does_not_recompute_on_second_call(conn, monkeypatch):
    monkeypatch.setattr(commentary.config, "anthropic_api_key", lambda: None)
    calls = {"n": 0}
    real_build = commentary.build_weekly_facts

    def counting_build(*args, **kwargs):
        calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(commentary, "build_weekly_facts", counting_build)

    first = commentary.get_or_generate_weekly_recap(conn, 2099, 1)
    second = commentary.get_or_generate_weekly_recap(conn, 2099, 1)
    assert calls["n"] == 1  # only the first call actually computed facts
    assert first["commentary"] == second["commentary"]


def test_get_or_generate_recap_falls_back_to_placeholder_on_claude_failure(conn, monkeypatch):
    monkeypatch.setattr(commentary.config, "anthropic_api_key", lambda: "fake-key")

    def boom(facts, api_key):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(commentary, "generate_claude_commentary", boom)
    result = commentary.get_or_generate_weekly_recap(conn, 2099, 1)
    assert result["source"] == "placeholder"


def test_get_or_generate_recap_uses_claude_when_available(conn, monkeypatch):
    monkeypatch.setattr(commentary.config, "anthropic_api_key", lambda: "fake-key")
    monkeypatch.setattr(commentary, "generate_claude_commentary", lambda facts, api_key: "HEADLINE\nFake AI recap")
    result = commentary.get_or_generate_weekly_recap(conn, 2099, 1)
    assert result["source"] == "claude"
    assert result["commentary"] == "HEADLINE\nFake AI recap"


def test_force_regenerate_overwrites_existing_recap(conn, monkeypatch):
    monkeypatch.setattr(commentary.config, "anthropic_api_key", lambda: None)
    commentary.get_or_generate_weekly_recap(conn, 2099, 1)
    monkeypatch.setattr(commentary.config, "anthropic_api_key", lambda: "fake-key")
    monkeypatch.setattr(commentary, "generate_claude_commentary", lambda facts, api_key: "HEADLINE\nNew version")
    result = commentary.get_or_generate_weekly_recap(conn, 2099, 1, force_regenerate=True)
    assert result["source"] == "claude"
    assert result["commentary"] == "HEADLINE\nNew version"
    count = conn.execute("SELECT COUNT(*) FROM weekly_recaps WHERE season_id = 2099 AND week = 1").fetchone()[0]
    assert count == 1  # upsert, not a duplicate row
