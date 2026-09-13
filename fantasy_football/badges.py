"""Configurable badge thresholds. Fraud Index = Actual Win% - All-Play
Win% (see fantasy_football/metrics/season_metrics.py). Kept here, not
hardcoded inline in a page, so thresholds are easy to tune later.

A POSITIVE Fraud Index means winning more than the underlying All-Play
quality would predict (a "fraud" - benefiting from schedule luck).
A NEGATIVE one means the mirror image: playing well by All-Play but
NOT winning as much as deserved - genuinely unlucky, not "legit" by
default. Originally only the positive side had real tiers and every
negative value silently fell through to LEGIT regardless of how
unlucky - fixed by mirroring the same thresholds onto the negative
side (2026-09-13)."""
from __future__ import annotations

FRAUD_THRESHOLDS = [
    (0.25, "GENERATIONAL FRAUD"),
    (0.15, "FRAUD WATCH"),
    (0.05, "SLIGHTLY SUSPICIOUS"),
]
LUCK_THRESHOLDS = [
    (-0.25, "SNAKEBIT"),
    (-0.15, "UNLUCKY"),
    (-0.05, "SLIGHTLY UNLUCKY"),
]
FRAUD_DEFAULT = "LEGIT"


def fraud_badge(fraud_index: float | None) -> str:
    if fraud_index is None:
        return FRAUD_DEFAULT
    for threshold, label in FRAUD_THRESHOLDS:
        if fraud_index >= threshold:
            return label
    for threshold, label in LUCK_THRESHOLDS:
        if fraud_index <= threshold:
            return label
    return FRAUD_DEFAULT
