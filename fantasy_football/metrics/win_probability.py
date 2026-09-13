"""Custom win probability for a future matchup - explicitly NOT ESPN's own
projection (spec requirement: keep the two clearly distinguished).

Method (analytical, not simulation - both are spec-acceptable):
Expected score = 60% season PPG + 40% last-3-week PPG. Each team's score
is modeled as Normal(expected_score, weekly_stdev) using that team's own
observed regular-season scoring stdev. The home team's margin (A - B) is
then Normal(mu_a - mu_b, sqrt(var_a + var_b)), so P(A wins) is just that
distribution's mass above zero - a standard closed-form result, no need
for a Monte Carlo here (Phase 7's playoff simulation is where the spec
explicitly requires >=10,000-sim Monte Carlo; this is a lighter-weight,
week-to-week estimate).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MIN_STDEV = 5.0  # a team with 0-1 games played has no real variance yet;
                  # flooring it avoids a falsely overconfident probability


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass(frozen=True)
class TeamProjection:
    team_pk: int
    expected_score: float
    stdev: float


def expected_score(season_ppg: float, last3_ppg: float) -> float:
    return 0.6 * season_ppg + 0.4 * last3_ppg


def win_probability(team_a: TeamProjection, team_b: TeamProjection) -> float:
    """Probability team_a beats team_b."""
    mu_diff = team_a.expected_score - team_b.expected_score
    var_a = max(team_a.stdev, MIN_STDEV) ** 2
    var_b = max(team_b.stdev, MIN_STDEV) ** 2
    sigma_diff = math.sqrt(var_a + var_b)
    if sigma_diff == 0:
        return 0.5
    return _normal_cdf(mu_diff / sigma_diff)
