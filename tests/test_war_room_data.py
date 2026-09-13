"""Unit tests for fantasy_football.war_room_data - pure logic only
(blend_trade_value), no network/DB calls per PROJECT_BRIEF testing
requirements. The rest of war_room_data.py (live FantasyPros/ESPN calls,
SQLite reads) is exercised via in-browser/AppTest validation instead,
same convention as dashboard_data.py's live-data functions."""
import pytest

from fantasy_football.war_room_data import blend_trade_value


def test_blends_both_signals_when_present():
    value = blend_trade_value(1.0, 0.5, "WR", {})
    # weighted average of 1.0 (weight 40) and 0.5 (weight 15): (40+7.5)/55
    assert value == pytest.approx((40 * 1.0 + 15 * 0.5) / 55)


def test_falls_back_to_only_present_signal_without_penalizing_missing_one():
    fp_only = blend_trade_value(0.8, None, "WR", {})
    assert fp_only == pytest.approx(0.8)

    espn_only = blend_trade_value(None, 0.8, "WR", {})
    assert espn_only == pytest.approx(0.8)


def test_zero_when_no_signal_present_at_all():
    assert blend_trade_value(None, None, "WR", {}) == 0.0


def test_fantasypros_signal_weighted_more_than_espn_signal():
    # same score on either signal alone should give the same value either
    # way (weights only matter when they DISAGREE) - so disagree them:
    fp_leaning = blend_trade_value(1.0, 0.0, "WR", {})
    espn_leaning = blend_trade_value(0.0, 1.0, "WR", {})
    assert fp_leaning > espn_leaning


def test_superflex_scarcity_multiplier_boosts_qb_value():
    flat = blend_trade_value(0.8, 0.8, "QB", {})
    superflex = blend_trade_value(0.8, 0.8, "QB", {"QB": 1.5})
    assert superflex == pytest.approx(flat * 1.5)


def test_scarcity_multiplier_does_not_affect_other_positions():
    scarcity = {"QB": 1.5}
    rb = blend_trade_value(0.6, 0.6, "RB", scarcity)
    rb_no_scarcity = blend_trade_value(0.6, 0.6, "RB", {})
    assert rb == pytest.approx(rb_no_scarcity)
