"""Roster Strength: a forward-looking "how good is this roster right now"
metric, distinct from Power Score (which is backward-looking, based on
results already produced). FantasyPros ROS rankings were added
2026-09-13 once the user obtained a licensed API key - before that, v1
shipped ESPN-only since FantasyPros' Terms of Use prohibit automated
reproduction of their rankings without one (see PROJECT_BRIEF.md's "Data
sourcing decision").

Signals (weights carried over proportionally from the original 4-source
design: FantasyPros ROS 40/100, ESPN weekly projection 20/100 - Yahoo's
25/100 and ESPN's season-to-date positional rank's 15/100 both dropped
(see below), remaining 2 renormalized to sum to 1):
- weekly_projection: this week's ESPN pre-game point projection,
  percentile-ranked WITHIN POSITION across all rostered players
  league-wide that week (comparing a projected QB score to a projected
  TE score directly would be meaningless).
- fantasypros_ros: FantasyPros' rest-of-season consensus positional rank
  (expert-panel ECR, genuinely forward-looking), converted to a bounded
  0-1 score via a smooth decay - not a percentile, since we don't
  reliably know the total ranked population size. Only present when
  FANTASYPROS_API_KEY is configured and the player could be matched to
  an ESPN player_id (see fantasy_football/player_matching.py) - a
  player missing this signal falls back entirely to weekly_projection,
  per blend() below, not a fabricated value.

A third ESPN signal (season-to-date positional rank, `posRank` from
`player_info()`) was dropped 2026-09-15 - a real correctness fix, not
just a simplification. It was backward-looking (cumulative points
scored SO FAR this season), directly contradicting this metric's own
forward-looking design goal, and its useful information was already
largely subsumed by fantasypros_ros: FantasyPros' analysts already
factor in season-to-date production when forming their ROS consensus,
with the added benefit of accounting for matchups/injuries/depth-chart
context going forward that a raw cumulative-points snapshot can't. Early
in a season especially, it was also a noisy small-sample signal - the
same fundamental problem behind the Playoff Odds week-1-overconfidence
fix a few commits earlier. User's own framing when asking for it to go:
"Why do I care about performance to date in this metric?"

Starter vs. bench weighting decays over the season to reflect bye-week
insurance value fading (NFL byes run roughly weeks 5-14), but keeps a
floor since injury-replacement value never goes away - see
bench_weight_for_week().
"""
from __future__ import annotations

import pandas as pd

# Proportional to the original 4-source weights (FantasyPros=40, ESPN
# weekly=20), Yahoo (25) and ESPN season-to-date rank (15) both dropped
# and the remaining 2 renormalized to sum to 1 (roughly a 1:2 split).
SIGNAL_WEIGHTS = {
    "weekly_projection": 20 / 60,
    "fantasypros_ros": 40 / 60,
}

BENCH_FLOOR = 0.10
BENCH_PEAK_EXTRA = 0.25
RANK_DECAY_K = 12.0  # rank 1 -> 1.0, rank 13 -> 0.5, rank 25 -> 0.33, ...


def bench_weight_for_week(week: int, reg_season_count: int = 14) -> float:
    """Bench's share of Roster Strength. Peaks at FLOOR+PEAK_EXTRA in
    week 1 (full bye-season + injury insurance value ahead), decays
    linearly to FLOOR by the last regular-season week (byes are over,
    only injury-replacement value remains) and holds at FLOOR through
    the playoffs (injuries don't stop for the playoffs either)."""
    span = max(reg_season_count - 1, 1)
    decay = max(0.0, min(1.0, (reg_season_count - week) / span))
    return BENCH_FLOOR + BENCH_PEAK_EXTRA * decay


def rank_to_score(pos_rank) -> float | None:
    """Bounded 0-1 "how good" score from a positional rank, without
    needing to know the total ranked population size."""
    if pos_rank is None or pd.isna(pos_rank) or pos_rank <= 0:
        return None
    return 1.0 / (1.0 + (pos_rank - 1) / RANK_DECAY_K)


def compute_player_values(roster_df: pd.DataFrame) -> pd.DataFrame:
    """roster_df: one row per rostered player for a given team-week, with
    columns team_pk, player_id, position, slot_position, is_starter,
    projected_points, and (optionally, may be absent or all-NaN)
    fp_pos_rank. Adds projection_percentile, fp_rank_score, and the
    blended player_value (0-1).

    Signals renormalize over whichever of the 2 are actually present for
    a player (e.g. FantasyPros couldn't match them) rather than treating
    a missing signal as a zero - see blend() below."""
    df = roster_df.copy()
    df["projection_percentile"] = df.groupby("position")["projected_points"].rank(pct=True)
    df["fp_rank_score"] = df["fp_pos_rank"].apply(rank_to_score) if "fp_pos_rank" in df.columns else None

    def blend(row):
        signals = [
            (SIGNAL_WEIGHTS["weekly_projection"], row["projection_percentile"]),
            (SIGNAL_WEIGHTS["fantasypros_ros"], row["fp_rank_score"]),
        ]
        present = [(w, v) for w, v in signals if pd.notna(v)]
        if not present:
            return 0.0
        total_weight = sum(w for w, _ in present)
        return sum(w * v for w, v in present) / total_weight

    df["player_value"] = df.apply(blend, axis=1)
    return df


def compute_team_roster_strength(roster_df: pd.DataFrame, week: int, reg_season_count: int = 14) -> pd.DataFrame:
    """roster_df must already have `player_value` (see
    compute_player_values). IR-slot players are excluded entirely (can't
    play, so they contribute to neither starter nor bench value). Returns
    one row per team_pk: starter_value, bench_value, weights, roster_strength (0-100)."""
    df = roster_df[roster_df["slot_position"] != "IR"]
    b_weight = bench_weight_for_week(week, reg_season_count)
    s_weight = 1 - b_weight

    rows = []
    for team_pk, g in df.groupby("team_pk"):
        starters = g[g["is_starter"] == 1]
        bench = g[g["is_starter"] == 0]
        starter_value = starters["player_value"].mean() if not starters.empty else 0.0
        bench_value = bench["player_value"].mean() if not bench.empty else 0.0
        rows.append(
            {
                "team_pk": team_pk,
                "starter_value": starter_value,
                "bench_value": bench_value,
                "starter_weight": s_weight,
                "bench_weight": b_weight,
                "roster_strength": 100 * (s_weight * starter_value + b_weight * bench_value),
            }
        )
    return pd.DataFrame(rows, columns=["team_pk", "starter_value", "bench_value", "starter_weight", "bench_weight", "roster_strength"])
