"""Unit tests for fantasy_football.playoff_odds_snapshots - pure logic
only (snapshots_to_rows, biggest_mover). load_snapshots/save_snapshot
are live-data functions (GitHub API calls), validated via AppTest/live
checks instead, per PROJECT_BRIEF testing convention."""
import pytest

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
