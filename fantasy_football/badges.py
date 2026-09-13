"""Configurable badge thresholds. Fraud Index = Actual Win% - All-Play
Win% (see fantasy_football/metrics/season_metrics.py). Kept here, not
hardcoded inline in a page, so thresholds are easy to tune later."""
from __future__ import annotations

FRAUD_THRESHOLDS = [
    (0.25, "GENERATIONAL FRAUD"),
    (0.15, "FRAUD WATCH"),
    (0.05, "SLIGHTLY SUSPICIOUS"),
]
FRAUD_DEFAULT = "LEGIT"


def fraud_badge(fraud_index: float | None) -> str:
    if fraud_index is None:
        return FRAUD_DEFAULT
    for threshold, label in FRAUD_THRESHOLDS:
        if fraud_index >= threshold:
            return label
    return FRAUD_DEFAULT
