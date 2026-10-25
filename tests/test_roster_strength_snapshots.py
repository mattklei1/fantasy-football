"""Unit tests for fantasy_football.roster_strength_snapshots - pure
blend logic only. load_latest_ema/update_stored_ema/get_smoothed_prior
are live-data functions (GitHub API calls, plus a DB read for
get_smoothed_prior), validated via live checks instead, per
PROJECT_BRIEF testing convention (same as playoff_odds_snapshots.py's
load_snapshots/save_snapshot)."""
import pytest

from fantasy_football.roster_strength_snapshots import EMA_WEIGHT_CURRENT, _blend


def test_blend_uses_raw_value_when_nothing_stored_yet():
    # first-ever run this season - nothing to smooth against, so the
    # raw value passes through unchanged.
    raw = {1: 50.0, 2: 60.0}
    assert _blend(raw, None) == raw
    assert _blend(raw, {}) == raw


def test_blend_weights_toward_current_value():
    raw = {1: 80.0}
    stored = {"1": 40.0}
    blended = _blend(raw, stored)
    expected = EMA_WEIGHT_CURRENT * 80.0 + (1 - EMA_WEIGHT_CURRENT) * 40.0
    assert blended[1] == pytest.approx(expected)
    # sanity: result sits strictly between the two inputs, closer to raw
    assert 40.0 < blended[1] < 80.0
    assert abs(blended[1] - 80.0) < abs(blended[1] - 40.0)


def test_blend_falls_back_to_raw_for_a_team_with_no_prior_stored_value():
    # a new team (expansion, or first time this team_pk shows up) has
    # nothing to smooth against yet - starts at its own raw value.
    raw = {1: 50.0, 2: 70.0}
    stored = {"1": 55.0}  # team 2 has no stored entry
    blended = _blend(raw, stored)
    assert blended[2] == 70.0
    assert blended[1] != 50.0  # team 1 DID get smoothed


def test_blend_converges_toward_a_stable_value_over_repeated_calls():
    # simulates several days of the same real raw value - the stored
    # EMA should converge toward it, not oscillate or diverge.
    stored = {"1": 20.0}
    raw = {1: 80.0}
    for _ in range(20):
        blended = _blend(raw, stored)
        stored = {"1": blended[1]}
    assert stored["1"] == pytest.approx(80.0, abs=0.01)
