"""Unit tests for fantasy_football.matchup_snapshots - pure logic only
(snapshots_to_rows, biggest_mover, benchmark_ticks). capture_snapshot/
append_snapshot/load_snapshots are live-data functions (ESPN + GitHub
API calls), validated via AppTest/live checks instead, per PROJECT_BRIEF
testing convention."""
import pytest

from fantasy_football.matchup_snapshots import benchmark_ticks, biggest_mover, snapshots_to_rows


def _snap(ts, **teams):
    return {"timestamp": ts, "season": 2026, "week": 1, "teams": teams}


def _team(name, win_prob, proj, make_it=None):
    return {"team_name": name, "win_probability": win_prob, "projected_score": proj, "p_making_it": make_it}


# --- snapshots_to_rows ---------------------------------------------------

def test_snapshots_to_rows_flattens_every_team_at_every_timestamp():
    snapshots = [
        _snap("2026-09-18T00:00:00+00:00", **{"1": _team("A", 0.6, 100.0), "2": _team("B", 0.4, 90.0)}),
        _snap("2026-09-18T00:10:00+00:00", **{"1": _team("A", 0.65, 105.0), "2": _team("B", 0.35, 92.0)}),
    ]
    rows = snapshots_to_rows(snapshots)
    assert len(rows) == 4
    first = rows[0]
    assert first["team_name"] == "A"
    assert first["win_probability"] == 0.6
    assert first["projected_score"] == 100.0
    assert first["timestamp"] == "2026-09-18T00:00:00+00:00"


def test_snapshots_to_rows_empty_input_is_empty_output():
    assert snapshots_to_rows([]) == []


# --- biggest_mover ---------------------------------------------------------

def test_biggest_mover_none_with_fewer_than_two_snapshots():
    assert biggest_mover([]) is None
    assert biggest_mover([_snap("t1", **{"1": _team("A", 0.5, 100.0)})]) is None


def test_biggest_mover_picks_largest_absolute_delta():
    snapshots = [
        _snap("t1", **{"1": _team("A", 0.50, 100.0), "2": _team("B", 0.50, 100.0)}),
        _snap("t2", **{"1": _team("A", 0.55, 102.0), "2": _team("B", 0.18, 90.0)}),
    ]
    mover = biggest_mover(snapshots, metric="win_probability")
    assert mover["team_name"] == "B"
    assert mover["delta"] == pytest.approx(-0.32)


def test_biggest_mover_handles_a_team_that_only_appears_in_the_latest_snapshot():
    snapshots = [
        _snap("t1", **{"1": _team("A", 0.5, 100.0)}),
        _snap("t2", **{"1": _team("A", 0.5, 100.0), "2": _team("B", 0.9, 150.0)}),
    ]
    # team B has no prior reading to diff against - should be skipped, not crash
    mover = biggest_mover(snapshots)
    assert mover is None or mover["team_name"] == "A"


def test_biggest_mover_respects_metric_argument():
    snapshots = [
        _snap("t1", **{"1": _team("A", 0.50, 100.0, make_it=0.5)}),
        _snap("t2", **{"1": _team("A", 0.50, 100.0, make_it=0.9)}),
    ]
    mover = biggest_mover(snapshots, metric="p_making_it")
    assert mover["team_name"] == "A"
    assert mover["delta"] == pytest.approx(0.4)


# --- benchmark_ticks -------------------------------------------------------

def test_benchmark_ticks_empty_for_no_snapshots():
    assert benchmark_ticks([]) == []


def test_benchmark_ticks_covers_a_full_thursday_through_monday_week():
    # 2026-09-17 is a Thursday, 2026-09-14 (Mon) already passed - use a
    # real Thu -> following Mon span
    snapshots = [
        _snap("2026-09-17T23:00:00+00:00"),  # Thu evening UTC
        _snap("2026-09-22T02:00:00+00:00"),  # Mon night UTC (following Monday)
    ]
    ticks = benchmark_ticks(snapshots)
    labels = [label for _, label in ticks]
    assert labels == ["Post-TNF", "Post-10am games", "Post-1pm games", "Post-SNF", "Post-MNF"]


def test_benchmark_ticks_only_includes_weekdays_actually_spanned():
    # a single Thursday-only snapshot shouldn't produce Sunday/Monday ticks
    snapshots = [_snap("2026-09-17T20:00:00+00:00")]
    ticks = benchmark_ticks(snapshots)
    labels = [label for _, label in ticks]
    assert labels == ["Post-TNF"]
