"""Unit tests for fantasy_football.badges - fraud_badge() must be
symmetric: a negative Fraud Index (genuinely unlucky - All-Play quality
not converting into wins) should get its own tier, not silently fall
through to LEGIT the way a merely-average team does."""
from fantasy_football.badges import fraud_badge


def test_fraud_badge_none_is_legit():
    assert fraud_badge(None) == "LEGIT"


def test_fraud_badge_average_is_legit():
    assert fraud_badge(0.0) == "LEGIT"
    assert fraud_badge(0.049) == "LEGIT"
    assert fraud_badge(-0.049) == "LEGIT"


def test_fraud_badge_positive_tiers():
    assert fraud_badge(0.06) == "SLIGHTLY SUSPICIOUS"
    assert fraud_badge(0.16) == "FRAUD WATCH"
    assert fraud_badge(0.30) == "GENERATIONAL FRAUD"


def test_fraud_badge_negative_tiers_are_not_legit():
    assert fraud_badge(-0.06) == "SLIGHTLY UNLUCKY"
    assert fraud_badge(-0.16) == "UNLUCKY"
    assert fraud_badge(-0.30) == "SNAKEBIT"
