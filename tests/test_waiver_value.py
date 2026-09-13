"""Unit tests for fantasy_football.metrics.waiver_value - pure, no
network calls (per PROJECT_BRIEF testing requirements)."""
import pytest

from fantasy_football.metrics.waiver_value import (
    MAX_BID_PCT_OF_BUDGET,
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


def test_suggested_bid_none_without_a_rank():
    result = suggested_bid(None, 50.0, "WR", {}, budget=200.0)
    assert result is None


def test_suggested_bid_top_ranked_player_costs_more_than_replacement_level():
    scarcity = {}
    top = suggested_bid(1, 90.0, "WR", scarcity, budget=200.0)
    replacement = suggested_bid(80, 5.0, "WR", scarcity, budget=200.0)
    assert top > replacement
    assert top <= MAX_BID_PCT_OF_BUDGET * 200.0


def test_suggested_bid_never_exceeds_budget_ceiling():
    # an absurdly good rank + full ownership should still be capped
    scarcity = {"QB": 1.5}
    result = suggested_bid(1, 100.0, "QB", scarcity, budget=200.0)
    assert result <= MAX_BID_PCT_OF_BUDGET * 200.0


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
