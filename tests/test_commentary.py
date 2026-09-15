"""Unit tests for fantasy_football.commentary - builds a minimal
in-memory SQLite DB via db.init_db() rather than mocking SQL, so these
exercise the real queries. No live Claude/ESPN calls (per PROJECT_BRIEF
testing requirements) - the Claude path is exercised with a monkeypatched
generate_claude_commentary, never the real API."""
import sqlite3

import numpy as np
import pandas as pd
import pytest

from fantasy_football import commentary, db
from fantasy_football.metrics.playoff_sim import simulate_season


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

    # week 1 starting lineups for team 1 (Team A) - used by starting_rosters
    c.execute("INSERT INTO players (player_id, player_name, default_position) VALUES (1, 'Star QB', 'QB')")
    c.execute("INSERT INTO players (player_id, player_name, default_position) VALUES (2, 'Bench RB', 'RB')")
    c.execute(
        "INSERT INTO weekly_rosters (season_id, week, team_pk, player_id, slot_position, is_starter, eligible_slots) "
        "VALUES (2099, 1, 1, 1, 'QB', 1, '[\"QB\", \"BE\"]')"
    )
    c.execute(
        "INSERT INTO weekly_rosters (season_id, week, team_pk, player_id, slot_position, is_starter, eligible_slots) "
        "VALUES (2099, 1, 1, 2, 'BE', 0, '[\"RB\", \"BE\"]')"
    )
    c.execute("INSERT INTO player_week_scores (season_id, week, player_id, points) VALUES (2099, 1, 1, 45.2)")

    # a genuine bad beat for team4 (Team D, lost 110-120 to Team C): its
    # starting RB was projected for 25 but scored only 5 - a 20-point
    # shortfall that alone would have flipped the matchup (110-5+25=130 > 120)
    c.execute("INSERT INTO players (player_id, player_name, default_position) VALUES (3, 'Hurt Star', 'RB')")
    c.execute(
        "INSERT INTO weekly_rosters (season_id, week, team_pk, player_id, slot_position, is_starter, eligible_slots) "
        "VALUES (2099, 1, 4, 3, 'RB', 1, '[\"RB\", \"BE\"]')"
    )
    c.execute(
        "INSERT INTO player_week_scores (season_id, week, player_id, points, projected_points) "
        "VALUES (2099, 1, 3, 5.0, 25.0)"
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


def test_starting_rosters_includes_starters_only_with_real_points(conn):
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    team_a_roster = next(r for r in facts["starting_rosters"] if r["team"]["team_name"] == "Team A")
    names = {p["name"] for p in team_a_roster["players"]}
    assert names == {"Star QB"}  # Bench RB is is_starter=0, excluded
    star = next(p for p in team_a_roster["players"] if p["name"] == "Star QB")
    assert star["points"] == pytest.approx(45.2)
    assert star["position"] == "QB"


def test_starting_rosters_empty_for_team_with_no_roster_data(conn):
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    team_b_rosters = [r for r in facts["starting_rosters"] if r["team"]["team_name"] == "Team B"]
    assert team_b_rosters == []  # no weekly_rosters rows inserted for Team B in the fixture


def test_strip_preamble_removes_text_before_first_section_header():
    text = "I'll search for real bad beats now.\n\n**HEADLINE**\n\nWeek 1 recap here."
    assert commentary._strip_preamble(text) == "**HEADLINE**\n\nWeek 1 recap here."


def test_strip_preamble_leaves_clean_output_unchanged():
    text = "**HEADLINE**\n\nWeek 1 recap here."
    assert commentary._strip_preamble(text) == text


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


def test_generate_claude_commentary_raises_on_max_tokens_truncation(monkeypatch):
    # A recap cut off mid-sentence by the token limit is NOT a usable
    # recap - it must raise (so get_or_generate_weekly_recap falls back
    # to the placeholder) rather than silently return partial text, the
    # way a "refusal" stop_reason already does.
    import anthropic

    class FakeBlock:
        type = "text"
        text = "**HEADLINE**\n\nThis recap gets cut off mid-sen"

    class FakeResponse:
        stop_reason = "max_tokens"
        stop_details = None
        content = [FakeBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    facts = {"season": 2099, "week": 1, "awards": {"bad_beat": None}}
    with pytest.raises(RuntimeError, match="cut off"):
        commentary.generate_claude_commentary(facts, "fake-key")


def test_next_week_game_to_watch_uses_optimal_lineup_projection_not_ppg(conn):
    # give team1 a real week-2 projection for its one real slot-eligible
    # player (QB) - should flow straight into home_projected_score,
    # NOT a season-ppg-blend number (team1's week-1 ppg was 150.0,
    # nowhere close to this)
    conn.execute(
        "INSERT INTO player_week_scores (season_id, week, player_id, projected_points) VALUES (2099, 2, 1, 27.5)"
    )
    conn.commit()
    total = commentary._team_next_week_optimal_projection(
        conn, 2099, roster_week=1, next_week=2, team_pk=1,
        position_slot_counts={"QB": 1, "BE": 5}, free_agent_avg_by_position={},
    )
    assert total == pytest.approx(27.5)


def test_game_to_watch_picks_the_matchup_closest_to_50_50_by_new_projections(conn):
    # team1 vs team3 (week-2 matchup): give them a lopsided gap (27.5 vs
    # 0) - a blowout, not close. team2 vs team4: leave both at 0 (tied)
    # - the genuinely closest possible matchup, and the one that should
    # be picked even though team1's real number is much bigger.
    conn.execute(
        "INSERT INTO player_week_scores (season_id, week, player_id, projected_points) VALUES (2099, 2, 1, 27.5)"
    )
    conn.commit()
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    gtw = facts["next_week_game_to_watch"]
    assert {gtw["home"]["team_pk"], gtw["away"]["team_pk"]} == {2, 4}
    assert gtw["home_win_probability"] == pytest.approx(0.5)


def test_team_next_week_optimal_projection_ignores_bench_player_ineligible_for_the_slot(conn):
    # player 2 (Bench RB) is NOT eligible for the QB slot, so it should
    # never get picked even if it somehow had a huge next-week projection
    conn.execute(
        "INSERT INTO player_week_scores (season_id, week, player_id, projected_points) VALUES (2099, 2, 2, 99.0)"
    )
    conn.commit()
    total = commentary._team_next_week_optimal_projection(
        conn, 2099, roster_week=1, next_week=2, team_pk=1,
        position_slot_counts={"QB": 1, "BE": 5}, free_agent_avg_by_position={},
    )
    assert total == pytest.approx(0.0)  # player 1 (the only QB-eligible player) has no week-2 projection yet


def test_team_next_week_optimal_projection_falls_back_to_free_agent_average_for_a_zero_projected_slot(conn):
    total = commentary._team_next_week_optimal_projection(
        conn, 2099, roster_week=1, next_week=2, team_pk=1,
        position_slot_counts={"QB": 1, "BE": 5}, free_agent_avg_by_position={"QB": 15.0},
    )
    assert total == pytest.approx(15.0)


class _FakePlayer:
    def __init__(self, projected):
        self.stats = {2: {"projected_points": projected}}


class _FakeLeague:
    def __init__(self, by_position):
        self._by_position = by_position

    def free_agents(self, size, position):
        return self._by_position.get(position, [])


def test_free_agent_avg_projection_by_position_averages_and_skips_zeros():
    league = _FakeLeague(
        {
            "QB": [_FakePlayer(10.0), _FakePlayer(20.0), _FakePlayer(0.0)],
            "RB": [],
        }
    )
    result = commentary._free_agent_avg_projection_by_position(league, next_week=2)
    assert result["QB"] == pytest.approx(15.0)  # average of 10 and 20 - the 0.0 is excluded
    assert "RB" not in result  # no usable free agents at RB


def test_free_agent_avg_projection_by_position_tolerates_a_failing_position(monkeypatch):
    class BoomLeague:
        def free_agents(self, size, position):
            if position == "QB":
                raise RuntimeError("ESPN hiccup")
            return [_FakePlayer(12.0)]

    result = commentary._free_agent_avg_projection_by_position(BoomLeague(), next_week=2)
    assert "QB" not in result
    assert result.get("RB") == pytest.approx(12.0)


def test_game_to_watch_none_without_position_slot_counts(conn):
    conn.execute("UPDATE seasons SET position_slot_counts = NULL WHERE season_id = 2099")
    conn.commit()
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    assert facts["next_week_game_to_watch"] is None


def test_bad_beat_prompt_scopes_to_the_specific_bad_beat_player(conn):
    facts = commentary.build_weekly_facts(conn, 2099, 1)
    bad_beat = facts["awards"]["bad_beat"]
    assert bad_beat["player_name"] == "Hurt Star"
    assert bad_beat["flipped_matchup"] is True
    assert bad_beat["shortfall"] == pytest.approx(20.0)
    prompt = commentary._build_claude_prompt(facts)
    assert f"team_pk {bad_beat['team']['team_pk']!r}" in prompt
    assert "'Hurt Star'" in prompt
    assert "never cite news about a different player" in prompt


def test_bad_beat_prompt_falls_back_to_team_scoped_instructions_without_player_data():
    facts = {
        "season": 2099, "week": 1,
        "awards": {"bad_beat": {"team_pk": 4, "score": 95.0, "team": {"team_pk": 4, "team_name": "Team D", "manager_name": "Dan"}}},
        "biggest_fraud": None, "power_rank_movers": [], "next_week_game_to_watch": None, "starting_rosters": [],
    }
    prompt = commentary._build_claude_prompt(facts)
    assert "never cite a real news story about a player on a DIFFERENT team" in prompt
    assert "never add an 'honorable mention'" in prompt


def test_bad_beat_prompt_tells_claude_not_to_invent_one_when_null():
    facts = {
        "season": 2099, "week": 1, "awards": {"bad_beat": None}, "biggest_fraud": None,
        "power_rank_movers": [], "next_week_game_to_watch": None, "starting_rosters": [],
    }
    prompt = commentary._build_claude_prompt(facts)
    assert "Do NOT invent one" in prompt


def test_prompt_directs_manager_of_the_week_to_lineup_efficiency_not_blowouts():
    prompt = commentary._build_claude_prompt({
        "season": 2099, "week": 1, "awards": {"bad_beat": None}, "biggest_fraud": None,
        "power_rank_movers": [], "next_week_game_to_watch": None, "starting_rosters": [],
    })
    assert "awards.best_lineup_efficiency" in prompt
    assert "not who scored the most or won by the most" in prompt


def test_placeholder_manager_of_the_week_uses_lineup_efficiency():
    facts = {
        "week": 1,
        "awards": {
            "highest_score": None, "closest_game": None, "biggest_blowout": None, "bad_beat": None,
            "best_lineup_efficiency": {"team": {"team_name": "Team A", "manager_name": "Alice"}, "lineup_efficiency": 0.943},
            "smart_lineup_call": None, "coaching_disaster": None,
        },
        "biggest_fraud": None, "power_rank_movers": [], "next_week_game_to_watch": None,
    }
    text = commentary.generate_placeholder_commentary(facts)
    assert "Team A (Alice)" in text
    assert "94.3%" in text


def test_placeholder_coaching_disaster_reports_median_and_matchup_consequences():
    facts = {
        "week": 1,
        "awards": {
            "highest_score": None, "closest_game": None, "biggest_blowout": None, "bad_beat": None,
            "best_lineup_efficiency": None, "smart_lineup_call": None,
            "coaching_disaster": {
                "team": {"team_name": "Team D", "manager_name": "Dan"}, "points_left_on_bench": 12.3,
                "flipped_result": True, "optimal_beats_median": True,
            },
        },
        "biggest_fraud": None, "power_rank_movers": [], "next_week_game_to_watch": None,
    }
    text = commentary.generate_placeholder_commentary(facts)
    assert "would have won the matchup" in text
    assert "would have cleared the median" in text


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


# --- GAME OF THE WEEK / PLAYOFF ODDS ---------------------------------------

def _ts_row(pk, mw, ml, medw, medl, pf):
    return {
        "team_pk": pk, "season_ppg": pf, "last3_ppg": pf, "score_stdev": 15.0,
        "matchup_wins": mw, "matchup_losses": ml, "matchup_ties": 0,
        "median_wins": medw, "median_losses": medl, "median_ties": 0, "points_for": pf,
    }


def _eight_team_state():
    # 8 teams, 6 playoff spots, reg_season_count=1 with NO remaining
    # games - fully deterministic seeding (no Monte Carlo noise on
    # playoff_pct itself), same technique test_playoff_sim.py uses for
    # its own "no games remain" determinism test. Teams 1/3/5/7 went
    # 1-0 (matchup) AND above the week's median; teams 2/4/6/8 went 0-1
    # and below median - so win_pct is a clean 1.0 vs 0.0 split, with
    # points_for as the only tiebreaker (all distinct values).
    return pd.DataFrame(
        [
            _ts_row(1, 1, 0, 1, 0, 200.0), _ts_row(2, 0, 1, 0, 1, 50.0),
            _ts_row(3, 1, 0, 1, 0, 180.0), _ts_row(4, 0, 1, 0, 1, 60.0),
            _ts_row(5, 1, 0, 1, 0, 160.0), _ts_row(6, 0, 1, 0, 1, 70.0),
            _ts_row(7, 1, 0, 1, 0, 140.0), _ts_row(8, 0, 1, 0, 1, 80.0),
        ]
    )


_EIGHT_TEAM_NAMES = {i: {"team_name": f"Team{i}", "manager_name": f"Mgr{i}"} for i in range(1, 9)}
_EMPTY_REMAINING = pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk"])


def test_game_of_the_week_picks_the_matchup_with_the_biggest_real_playoff_swing():
    # Verified by hand and by direct simulation before writing this
    # test: with this exact team_state, flipping 1v2 (team1 crushed
    # team2, but team1 was SO far ahead on points_for he stays in even
    # at a hypothetical 0.5 win_pct, while team2 swings from a clean
    # "out" to "in") changes playoff_pct for team2 (0.0 -> 1.0, a real
    # 100-point swing); flipping 7v8 (the actual seed4/5 bubble game)
    # changes NOTHING - both teams make the field either way. The
    # "closest score margin" version of this fixture would have picked
    # 7v8 (60-point margin) over 1v2 (150-point margin) - the opposite
    # of what actually matters for anyone's playoff odds.
    team_state = _eight_team_state()
    actual = simulate_season(
        team_state, _EMPTY_REMAINING, median_scoring=True, reg_season_count=1,
        n_sims=2000, rng=np.random.default_rng(1),
    ).set_index("team_pk")
    matchups_week = pd.DataFrame(
        [
            {"week": 1, "home_team_pk": 1, "away_team_pk": 2, "home_score": 200.0, "away_score": 50.0},
            {"week": 1, "home_team_pk": 7, "away_team_pk": 8, "home_score": 140.0, "away_score": 80.0},
        ]
    )
    result = commentary._game_of_the_week(
        matchups_week, _EIGHT_TEAM_NAMES, team_state, _EMPTY_REMAINING, True, 1, actual,
    )
    assert result is not None
    assert {result["home"]["team_pk"], result["away"]["team_pk"]} == {1, 2}
    assert result["swung_team"]["team_pk"] == 2
    assert result["playoff_odds_swing"] == pytest.approx(1.0)
    # team2 actually LOST this game and it kept them out (0.0 real vs
    # 1.0 in the counterfactual where they win) - the real result hurt them
    assert result["swing_direction"] == "down"


def test_game_of_the_week_none_without_a_playoff_sim_baseline():
    matchups_week = pd.DataFrame(
        [{"week": 1, "home_team_pk": 1, "away_team_pk": 2, "home_score": 100.0, "away_score": 90.0}]
    )
    assert commentary._game_of_the_week(matchups_week, {}, None, None, None, None, None) is None


def test_game_of_the_week_none_with_no_matchups():
    team_state = _eight_team_state()
    actual = simulate_season(
        team_state, _EMPTY_REMAINING, median_scoring=True, reg_season_count=1, n_sims=500,
    ).set_index("team_pk")
    empty_matchups = pd.DataFrame(columns=["week", "home_team_pk", "away_team_pk", "home_score", "away_score"])
    assert commentary._game_of_the_week(
        empty_matchups, _EIGHT_TEAM_NAMES, team_state, _EMPTY_REMAINING, True, 1, actual
    ) is None


def test_playoff_odds_summary_top3_and_season_riser_vs_preseason(monkeypatch):
    team_state = _eight_team_state()
    actual = simulate_season(
        team_state, _EMPTY_REMAINING, median_scoring=True, reg_season_count=1,
        n_sims=2000, rng=np.random.default_rng(1),
    ).set_index("team_pk")
    # no real snapshot history at all (no Week 0, no last week) - forces
    # the season-long/weekly comparisons to fall back to the synthetic
    # Pre-draft fair-share baseline
    monkeypatch.setattr(commentary.playoff_odds_snapshots, "load_snapshots", lambda season: {})

    result = commentary._playoff_odds_summary(2099, week=1, names=_EIGHT_TEAM_NAMES, actual=actual)
    assert result is not None
    assert len(result["top3"]) == 3
    # all 3 of the top-3 must be real playoff locks (playoff_pct == 1.0)
    assert all(t["playoff_pct"] == pytest.approx(1.0) for t in result["top3"])
    assert result["weekly_compared_to"] == "preseason"
    assert result["season_compared_to"] == "preseason"
    # preseason baseline for 8 teams / 6 playoff spots = 6/8 = 0.75 -
    # team1 (a real 1.0 lock) rose the most above that baseline
    assert result["season_riser"]["delta"] == pytest.approx(0.25)


def test_playoff_odds_summary_prefers_real_week0_snapshot_over_preseason(monkeypatch):
    team_state = _eight_team_state()
    actual = simulate_season(
        team_state, _EMPTY_REMAINING, median_scoring=True, reg_season_count=1,
        n_sims=2000, rng=np.random.default_rng(1),
    ).set_index("team_pk")
    # Real actual playoff_pct for this fixture: teams {1,3,5,6,7,8}=1.0,
    # {2,4}=0.0. Give every OTHER playoff team a Week-0 baseline that
    # already matched its real outcome exactly (delta=0), and team1 a
    # Week-0 baseline well below its real 1.0 - the unambiguous riser.
    week0_rows = [
        {"team_pk": pk, "team_name": _EIGHT_TEAM_NAMES[pk]["team_name"],
         "playoff_pct": 0.5 if pk == 1 else (0.0 if pk in (2, 4) else 1.0),
         "bye_pct": 0.0, "seed1_pct": 0.0, "championship_pct": 0.0}
        for pk in range(1, 9)
    ]
    monkeypatch.setattr(commentary.playoff_odds_snapshots, "load_snapshots", lambda season: {"0": week0_rows})

    result = commentary._playoff_odds_summary(2099, week=1, names=_EIGHT_TEAM_NAMES, actual=actual)
    assert result["weekly_compared_to"] == "week0"
    assert result["season_compared_to"] == "week0"
    assert result["season_riser"]["team"]["team_pk"] == 1
    assert result["season_riser"]["delta"] == pytest.approx(0.5)  # 1.0 real - 0.5 real Week 0


def test_playoff_odds_summary_uses_real_last_week_snapshot_when_present(monkeypatch):
    team_state = _eight_team_state()
    actual = simulate_season(
        team_state, _EMPTY_REMAINING, median_scoring=True, reg_season_count=1,
        n_sims=2000, rng=np.random.default_rng(1),
    ).set_index("team_pk")
    # team2 was already projected at 100% last week, but this week's
    # real result dropped them to 0% - the biggest FALLER, even though
    # the preseason baseline would have shown a smaller drop
    last_week_rows = [
        {"team_pk": pk, "team_name": _EIGHT_TEAM_NAMES[pk]["team_name"], "playoff_pct": 1.0 if pk == 2 else 0.75,
         "bye_pct": 0.0, "seed1_pct": 0.0, "championship_pct": 0.0}
        for pk in range(1, 9)
    ]
    monkeypatch.setattr(
        commentary.playoff_odds_snapshots, "load_snapshots", lambda season: {"1": last_week_rows}
    )

    result = commentary._playoff_odds_summary(2099, week=2, names=_EIGHT_TEAM_NAMES, actual=actual)
    assert result["weekly_compared_to"] == "last_week"
    assert result["weekly_faller"]["team"]["team_pk"] == 2
    assert result["weekly_faller"]["delta"] == pytest.approx(-1.0)


def test_playoff_odds_summary_none_without_actual_sim():
    assert commentary._playoff_odds_summary(2099, 1, {}, None) is None


def test_playoff_sim_baseline_none_when_not_enough_real_teams(conn):
    # the fixture's own conn only has 4 real teams but declares
    # playoff_team_count=6 - simulate_season can't seed a 6-team bracket
    # from 4 teams, so this must degrade gracefully, not crash
    team_state, remaining, median_scoring, reg_season_count, actual = commentary._playoff_sim_baseline(
        conn, 2099, 1
    )
    assert team_state is None
    assert actual is None
