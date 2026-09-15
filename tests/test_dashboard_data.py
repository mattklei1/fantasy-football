"""Unit tests for fantasy_football.dashboard_data - pure logic only
(_live_remaining_stdev), no network/DB calls per PROJECT_BRIEF testing
requirements. The rest of dashboard_data.py (live ESPN calls, SQLite
reads) is exercised via in-browser/AppTest validation instead."""
import pytest

from fantasy_football.dashboard_data import MIN_REMAINING_STDEV_FRACTION, _live_remaining_stdev


def test_scales_by_sqrt_of_fraction_remaining():
    # half the projected total still undecided -> stdev scales by sqrt(0.5)
    result = _live_remaining_stdev(full_stdev=20.0, projected=100.0, score_so_far=50.0)
    assert result == pytest.approx(20.0 * (0.5 ** 0.5))


def test_floors_at_min_fraction_when_nearly_but_not_fully_done():
    # 1% of the projected total still undecided - real, nonzero uncertainty,
    # but sqrt(0.01)=0.1 is below the 0.15 floor, so the floor applies
    result = _live_remaining_stdev(full_stdev=20.0, projected=100.0, score_so_far=99.0)
    assert result == pytest.approx(20.0 * MIN_REMAINING_STDEV_FRACTION)


def test_is_exactly_zero_when_every_player_has_finished():
    # score_so_far caught up to projected - nothing left to play, so no
    # real uncertainty remains (user feedback 2026-09-15: "when their
    # players have all played, the range should be zero") - must NOT be
    # padded up to the MIN_REMAINING_STDEV_FRACTION floor.
    result = _live_remaining_stdev(full_stdev=20.0, projected=146.66, score_so_far=146.66)
    assert result == 0.0


def test_is_exactly_zero_when_a_late_stat_correction_pushes_score_above_projected():
    result = _live_remaining_stdev(full_stdev=20.0, projected=100.0, score_so_far=101.5)
    assert result == 0.0


def test_returns_full_stdev_when_projected_is_zero_or_negative():
    assert _live_remaining_stdev(full_stdev=20.0, projected=0.0, score_so_far=0.0) == 20.0
    assert _live_remaining_stdev(full_stdev=20.0, projected=-5.0, score_so_far=0.0) == 20.0
