"""Unit tests for fantasy_football.metrics.waiver_value - pure, no
network calls (per PROJECT_BRIEF testing requirements)."""
import pytest

from fantasy_football.metrics.waiver_value import (
    DEFAULT_CEILING,
    POSITION_CEILINGS,
    position_scarcity_multipliers,
    suggested_bid,
)


def test_scarcity_neutral_when_no_superflex():
    mult = position_scarcity_multipliers({"QB": 1, "OP": 0})
    assert mult["QB"] == pytest.approx(1.0)


def test_scarcity_boosts_qb_in_superflex():
    mult = position_scarcity_multipliers({"QB": 1, "OP": 1})
    assert mult["QB"] == pytest.approx(1.5)


def test_scarcity_missing_op_key_treated_as_zero():
    mult = position_scarcity_multipliers({"QB": 1})
    assert mult["QB"] == pytest.approx(1.0)


def test_scarcity_no_week_defaults_to_full_premium():
    mult = position_scarcity_multipliers({"QB": 1, "OP": 1})
    assert mult["QB"] == pytest.approx(1.5)


def test_scarcity_early_week_dampens_qb_premium():
    early = position_scarcity_multipliers({"QB": 1, "OP": 1}, week=1)
    full = position_scarcity_multipliers({"QB": 1, "OP": 1}, week=9)
    assert early["QB"] < full["QB"]
    assert full["QB"] == pytest.approx(1.5)
    # floor still applies before byes are even possible (week <= 4)
    assert early["QB"] == pytest.approx(1.0 + 0.5 * 0.4)


def test_scarcity_ramps_monotonically_from_week_4_to_9():
    weeks = [4, 5, 6, 7, 8, 9]
    values = [position_scarcity_multipliers({"QB": 1, "OP": 1}, week=w)["QB"] for w in weeks]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_scarcity_week_past_ramp_end_stays_at_full_premium():
    at_end = position_scarcity_multipliers({"QB": 1, "OP": 1}, week=9)
    late = position_scarcity_multipliers({"QB": 1, "OP": 1}, week=17)
    assert at_end["QB"] == pytest.approx(late["QB"])
    assert late["QB"] == pytest.approx(1.5)


def test_suggested_bid_none_without_a_rank():
    result = suggested_bid(None, 50.0, "WR", {}, budget=200.0)
    assert result is None


def test_suggested_bid_top_ranked_player_costs_more_than_replacement_level():
    scarcity = {}
    top = suggested_bid(1, 90.0, "WR", scarcity, budget=200.0)
    replacement = suggested_bid(80, 5.0, "WR", scarcity, budget=200.0)
    assert top > replacement
    assert top <= POSITION_CEILINGS["WR"] * 200.0


def test_suggested_bid_never_exceeds_its_position_ceiling():
    # an absurdly good rank + full ownership should still be capped
    scarcity = {"QB": 1.5}
    result = suggested_bid(1, 100.0, "QB", scarcity, budget=200.0)
    assert result <= POSITION_CEILINGS["QB"] * 200.0


def test_low_ceiling_positions_are_capped_far_below_qb():
    # the calibration fix this addresses: D/ST and K must never approach
    # QB-level suggested values, even for a rank-1/fully-owned player
    dst = suggested_bid(1, 100.0, "D/ST", {}, budget=200.0)
    k = suggested_bid(1, 100.0, "K", {}, budget=200.0)
    qb = suggested_bid(1, 100.0, "QB", {"QB": 1.5}, budget=200.0)
    assert dst <= POSITION_CEILINGS["D/ST"] * 200.0
    assert k <= POSITION_CEILINGS["K"] * 200.0
    assert dst < qb
    assert k < dst


def test_unknown_position_falls_back_to_default_ceiling():
    result = suggested_bid(1, 100.0, "P", {}, budget=200.0)
    assert result == pytest.approx(DEFAULT_CEILING * 200.0)


def test_suggested_bid_qb_costs_more_in_superflex_than_flat_format():
    flat = suggested_bid(5, 40.0, "QB", position_scarcity_multipliers({"QB": 1, "OP": 0}), budget=200.0)
    superflex = suggested_bid(5, 40.0, "QB", position_scarcity_multipliers({"QB": 1, "OP": 1}), budget=200.0)
    assert superflex > flat


def test_suggested_bid_scales_with_budget():
    scarcity = {}
    low_budget = suggested_bid(3, 60.0, "RB", scarcity, budget=100.0)
    high_budget = suggested_bid(3, 60.0, "RB", scarcity, budget=200.0)
    assert high_budget == pytest.approx(low_budget * 2)


def test_suggested_bid_non_qb_position_unaffected_by_qb_scarcity():
    scarcity = position_scarcity_multipliers({"QB": 1, "OP": 1})
    rb = suggested_bid(5, 40.0, "RB", scarcity, budget=200.0)
    rb_no_superflex = suggested_bid(5, 40.0, "RB", {}, budget=200.0)
    assert rb == pytest.approx(rb_no_superflex)
