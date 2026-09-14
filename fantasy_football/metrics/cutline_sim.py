"""Monte Carlo model for "will this team finish at/above the median
(top-half) scoring cutline THIS WEEK" - same Normal(mu, stdev) per-team
scoring model as win_probability.py/playoff_sim.py (mu = current live
projected total, stdev = team's own season scoring stdev, so this stays
consistent with the rest of the app's probability language). Unlike a
single head-to-head matchup, "top half of the league this week" is a
relative-rank question across every team at once, which isn't a closed-
form calculation - Monte Carlo (independent draws per team per trial,
same >=10,000-trial convention as playoff_sim.py) is used instead of an
order-statistics approximation, kept intentionally simple. Pure/DB-free
like the rest of metrics/ - dashboard_data.py supplies the real live
projections and stdevs.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_N_SIMS = 10_000

# scipy.stats.norm.ppf(0.10) / ppf(0.90) - hardcoded so this module has no
# scipy dependency for two constants.
Z_10TH_PERCENTILE = -1.2815515655446004
Z_90TH_PERCENTILE = 1.2815515655446004


@dataclass(frozen=True)
class TeamScoreModel:
    team_pk: int
    mean: float
    stdev: float


def percentile_range(team: TeamScoreModel) -> tuple[float, float]:
    """(p10, p90) of team's own Normal(mean, stdev) final-score
    distribution, floored at 0 (a real score can't go negative). This is
    each team's own marginal distribution - it doesn't depend on any
    other team, so it's closed-form, not simulated."""
    p10 = max(0.0, team.mean + Z_10TH_PERCENTILE * team.stdev)
    p90 = max(0.0, team.mean + Z_90TH_PERCENTILE * team.stdev)
    return p10, p90


def simulate_cutline_probabilities(
    teams: list[TeamScoreModel], n_sims: int = DEFAULT_N_SIMS, rng: np.random.Generator | None = None,
) -> dict[int, float]:
    """Returns {team_pk: probability of finishing in the league's top
    half by score this week} via independent Monte Carlo draws per team
    per trial. "Top half" is the top len(teams)//2 finishers by score
    each trial - matches this league's real median-scoring bonus-win
    rule (top half of 12 teams = top 6). Ties (vanishingly unlikely with
    continuous draws) resolve however numpy's strict `>` comparison
    happens to land, same as playoff_sim.py's week-to-week tie handling."""
    if not teams:
        return {}
    rng = rng if rng is not None else np.random.default_rng()
    n_teams = len(teams)
    cutoff = n_teams // 2

    means = np.array([t.mean for t in teams])
    stdevs = np.array([t.stdev for t in teams])
    team_pks = [t.team_pk for t in teams]

    draws = rng.normal(loc=means[:, None], scale=stdevs[:, None], size=(n_teams, n_sims))
    draws = np.clip(draws, 0, None)

    # beats[i, j, s] = True if team j outscored team i in trial s;
    # summing over j gives team i's rank that trial (0 = best)
    beats = draws[None, :, :] > draws[:, None, :]
    rank = beats.sum(axis=1)
    made_it = rank < cutoff

    return {pk: float(made_it[i].mean()) for i, pk in enumerate(team_pks)}
