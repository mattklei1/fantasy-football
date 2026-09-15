"""Unit tests for fantasy_football.playoff_odds_snapshots - pure logic
only (snapshots_to_rows, biggest_mover) plus DB-backed tests for
compute_week0_team_state/compute_week0_snapshot_rows (a real in-memory
SQLite DB via db.init_db(), same pattern as test_commentary.py).
load_snapshots/save_snapshot are live-data functions (GitHub API calls),
validated via AppTest/live checks instead, per PROJECT_BRIEF testing
convention."""
import sqlite3

import pytest

from fantasy_football import db
from fantasy_football.playoff_odds_snapshots import biggest_mover, snapshots_to_rows


def _row(team_pk, name, championship_pct, playoff_pct=0.5, bye_pct=0.1, seed1_pct=0.05):
    return {
        "team_pk": team_pk, "team_name": name, "championship_pct": championship_pct,
        "playoff_pct": playoff_pct, "bye_pct": bye_pct, "seed1_pct": seed1_pct,
    }


# --- snapshots_to_rows -------------------------------------------------

def test_snapshots_to_rows_flattens_every_team_at_every_week():
    manifest = {
        "1": [_row(1, "A", 0.10), _row(2, "B", 0.05)],
        "2": [_row(1, "A", 0.15), _row(2, "B", 0.03)],
    }
    rows = snapshots_to_rows(manifest)
    assert len(rows) == 4
    weeks = sorted(r["week"] for r in rows)
    assert weeks == [1, 1, 2, 2]
    assert all("championship_pct" in r for r in rows)


def test_snapshots_to_rows_empty_input_is_empty_output():
    assert snapshots_to_rows({}) == []


def test_snapshots_to_rows_skips_unparseable_week_keys():
    manifest = {"not-a-week": [_row(1, "A", 0.1)], "2": [_row(1, "A", 0.2)]}
    rows = snapshots_to_rows(manifest)
    assert len(rows) == 1
    assert rows[0]["week"] == 2


# --- biggest_mover -------------------------------------------------------

def test_biggest_mover_none_with_fewer_than_two_weeks():
    assert biggest_mover({}) is None
    assert biggest_mover({"1": [_row(1, "A", 0.1)]}) is None


def test_biggest_mover_picks_largest_absolute_delta_between_last_two_weeks():
    manifest = {
        "1": [_row(1, "A", 0.10), _row(2, "B", 0.10)],
        "2": [_row(1, "A", 0.12), _row(2, "B", 0.30)],
    }
    mover = biggest_mover(manifest, metric="championship_pct")
    assert mover["team_name"] == "B"
    assert mover["delta"] == pytest.approx(0.20)


def test_biggest_mover_uses_the_two_most_recent_weeks_not_the_first_two():
    manifest = {
        "1": [_row(1, "A", 0.50)],
        "2": [_row(1, "A", 0.10)],  # huge week1->2 swing, should NOT be picked
        "3": [_row(1, "A", 0.12)],  # small week2->3 swing - this is the real answer
    }
    mover = biggest_mover(manifest, metric="championship_pct")
    assert mover["delta"] == pytest.approx(0.02)


def test_biggest_mover_handles_a_team_that_only_appears_in_the_latest_week():
    manifest = {
        "1": [_row(1, "A", 0.20)],
        "2": [_row(1, "A", 0.20), _row(2, "B", 0.40)],
    }
    # team B has no prior reading to diff against - should be skipped, not crash
    mover = biggest_mover(manifest, metric="championship_pct")
    assert mover is None or mover["team_name"] == "A"


def test_biggest_mover_respects_metric_argument():
    manifest = {
        "1": [_row(1, "A", 0.10, playoff_pct=0.50)],
        "2": [_row(1, "A", 0.11, playoff_pct=0.90)],
    }
    mover = biggest_mover(manifest, metric="playoff_pct")
    assert mover["team_name"] == "A"
    assert mover["delta"] == pytest.approx(0.40)


# --- preseason_baseline_rows -----------------------------------------------

def test_preseason_baseline_is_fair_share_for_every_team():
    from fantasy_football.playoff_odds_snapshots import preseason_baseline_rows

    team_names = {i: f"Team{i}" for i in range(1, 9)}  # 8 teams
    rows = preseason_baseline_rows(team_names, playoff_team_count=6)
    assert len(rows) == 8
    for r in rows:
        assert r["playoff_pct"] == pytest.approx(6 / 8)
        assert r["bye_pct"] == pytest.approx(2 / 8)
        assert r["seed1_pct"] == pytest.approx(1 / 8)
        assert r["championship_pct"] == pytest.approx(1 / 8)


def test_preseason_baseline_empty_for_no_teams():
    from fantasy_football.playoff_odds_snapshots import preseason_baseline_rows

    assert preseason_baseline_rows({}, playoff_team_count=6) == []


# --- build_chart_figure -----------------------------------------------------

def test_build_chart_figure_prepends_a_pre_draft_point_when_team_names_given():
    from fantasy_football.playoff_odds_snapshots import build_chart_figure

    manifest = {"1": [_row(1, "A", 0.20), _row(2, "B", 0.10)]}
    team_names = {1: "A", 2: "B"}
    fig = build_chart_figure(manifest, "championship_pct", team_names=team_names, playoff_team_count=6)
    trace_a = next(t for t in fig.data if t.name == "A")
    assert list(trace_a.x) == [-1, 1]  # pre-draft (-1) then week 1 - no real Week 0 in this manifest


def test_build_chart_figure_uses_real_week0_snapshot_when_present():
    from fantasy_football.playoff_odds_snapshots import build_chart_figure

    manifest = {"0": [_row(1, "A", 0.15)], "1": [_row(1, "A", 0.20)]}
    fig = build_chart_figure(manifest, "championship_pct", team_names={1: "A"}, playoff_team_count=6)
    trace_a = next(t for t in fig.data if t.name == "A")
    # pre-draft (-1, synthetic) + the REAL week-0 snapshot (0) + week 1 -
    # the real "0" entry must NOT be clobbered by the synthetic one
    assert list(trace_a.x) == [-1, 0, 1]
    assert list(trace_a.y) == [pytest.approx(1 / 1), pytest.approx(0.15), pytest.approx(0.20)]


def test_build_chart_figure_x_axis_labels_pre_draft_week0_and_post_week():
    from fantasy_football.playoff_odds_snapshots import build_chart_figure

    manifest = {"0": [_row(1, "A", 0.18)], "1": [_row(1, "A", 0.20)], "2": [_row(1, "A", 0.25)]}
    fig = build_chart_figure(manifest, "championship_pct", team_names={1: "A"}, playoff_team_count=6)
    assert list(fig.layout.xaxis.ticktext) == ["Pre-draft", "Week 0", "Post Wk1", "Post Wk2"]


def test_build_chart_figure_without_team_names_has_no_preseason_point():
    from fantasy_football.playoff_odds_snapshots import build_chart_figure

    manifest = {"1": [_row(1, "A", 0.20)]}
    fig = build_chart_figure(manifest, "championship_pct")
    trace_a = next(t for t in fig.data if t.name == "A")
    assert list(trace_a.x) == [1]


# --- compute_week0_team_state / compute_week0_snapshot_rows ----------------

def _round_robin_matchups(n_teams: int) -> list[tuple[int, int, int]]:
    """(week, home_team_pk, away_team_pk) for a standard circle-method
    round robin - n_teams-1 rounds, every team plays every other exactly
    once (n_teams even)."""
    teams = list(range(1, n_teams + 1))
    rows = []
    for week in range(1, n_teams):
        for i in range(n_teams // 2):
            rows.append((week, teams[i], teams[n_teams - 1 - i]))
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rows


@pytest.fixture
def week0_conn():
    """8 teams with DIFFERENT Week-1 optimal-lineup projections (a real
    roster-quality spread, team_pk N projected at 80+10*N points) and a
    full round-robin remaining schedule - playoff_team_count=6, so only
    some of the 8 teams make it, giving playoff_pct room to differentiate."""
    c = sqlite3.connect(":memory:")
    db.init_db(c)
    c.execute(
        "INSERT INTO seasons (season_id, league_id, reg_season_count, playoff_team_count, "
        "median_scoring, position_slot_counts) VALUES (2099, 1, 7, 6, 0, '{\"QB\": 1}')"
    )
    for i in range(1, 9):
        c.execute("INSERT INTO managers (manager_id, display_name) VALUES (?, ?)", (f"m{i}", f"Manager {i}"))
        c.execute(
            "INSERT INTO teams (id, season_id, espn_team_id, team_name) VALUES (?, 2099, ?, ?)",
            (i, i, f"Team {i}"),
        )
        c.execute("INSERT INTO team_owners (team_pk, manager_id) VALUES (?, ?)", (i, f"m{i}"))
        c.execute("INSERT INTO players (player_id, player_name, default_position) VALUES (?, ?, 'QB')", (i, f"QB {i}"))
        c.execute(
            "INSERT INTO weekly_rosters (season_id, week, team_pk, player_id, slot_position, is_starter, eligible_slots) "
            "VALUES (2099, 1, ?, ?, 'QB', 1, '[\"QB\"]')", (i, i),
        )
        c.execute(
            "INSERT INTO player_week_scores (season_id, week, player_id, projected_points) VALUES (2099, 1, ?, ?)",
            (i, 70.0 + i * 10.0),  # team 1 -> 80, team 8 -> 150
        )
    for week, home, away in _round_robin_matchups(8):
        c.execute(
            "INSERT INTO matchups (season_id, week, home_team_pk, away_team_pk, matchup_type, completed) "
            "VALUES (2099, ?, ?, ?, 'NONE', 0)", (week, home, away),
        )
    c.commit()
    return c


def test_compute_week0_team_state_uses_optimal_week1_lineup_projection(week0_conn):
    from fantasy_football.metrics.win_probability import MIN_STDEV
    from fantasy_football.playoff_odds_snapshots import compute_week0_team_state

    team_state = compute_week0_team_state(week0_conn, 2099, {"QB": 1}).set_index("team_pk")
    assert len(team_state) == 8
    for i in range(1, 9):
        assert team_state.loc[i, "season_ppg"] == pytest.approx(70.0 + i * 10.0)
        assert team_state.loc[i, "last3_ppg"] == pytest.approx(70.0 + i * 10.0)
        assert team_state.loc[i, "score_stdev"] == pytest.approx(MIN_STDEV)
        assert team_state.loc[i, "matchup_wins"] == 0
        assert team_state.loc[i, "matchup_losses"] == 0


def test_compute_week0_team_state_empty_without_week1_roster_data():
    from fantasy_football.playoff_odds_snapshots import compute_week0_team_state

    c = sqlite3.connect(":memory:")
    db.init_db(c)
    c.execute(
        "INSERT INTO seasons (season_id, league_id, reg_season_count, playoff_team_count, position_slot_counts) "
        "VALUES (2099, 1, 7, 6, '{\"QB\": 1}')"
    )
    c.commit()
    assert compute_week0_team_state(c, 2099, {"QB": 1}).empty


def test_compute_week0_snapshot_rows_differentiates_teams_by_roster_quality(week0_conn):
    # Regression test for a real bug (2026-09-15): before
    # shrinkage_games_played was wired in, every team's Week-1
    # projection was fully discarded (games_played=0 -> collapse to the
    # league average) and every real team landed at ~49-51% playoff
    # odds regardless of actual roster quality - user, correctly: "I
    # wouldn't expect all teams to be at 49-51% at week 0... we have a
    # pretty good idea of who will make the playoffs based on projected
    # score."
    from fantasy_football.playoff_odds_snapshots import compute_week0_snapshot_rows

    rows = compute_week0_snapshot_rows(week0_conn, 2099, n_sims=2000)
    assert rows is not None
    by_pk = {r["team_pk"]: r for r in rows}
    spread = max(r["playoff_pct"] for r in rows) - min(r["playoff_pct"] for r in rows)
    assert spread > 0.3  # a real, meaningful spread - not clustered near 50%
    # team 8 (150 projected) should clearly outrank team 1 (80 projected)
    assert by_pk[8]["playoff_pct"] > by_pk[1]["playoff_pct"]


def test_compute_week0_snapshot_rows_none_for_unsupported_playoff_team_count(week0_conn):
    from fantasy_football.playoff_odds_snapshots import compute_week0_snapshot_rows

    week0_conn.execute("UPDATE seasons SET playoff_team_count = 4 WHERE season_id = 2099")
    week0_conn.commit()
    assert compute_week0_snapshot_rows(week0_conn, 2099) is None


def test_compute_week0_snapshot_rows_none_without_week1_roster_data():
    from fantasy_football.playoff_odds_snapshots import compute_week0_snapshot_rows

    c = sqlite3.connect(":memory:")
    db.init_db(c)
    c.execute(
        "INSERT INTO seasons (season_id, league_id, reg_season_count, playoff_team_count, position_slot_counts) "
        "VALUES (2099, 1, 7, 6, '{\"QB\": 1}')"
    )
    c.commit()
    assert compute_week0_snapshot_rows(c, 2099) is None
