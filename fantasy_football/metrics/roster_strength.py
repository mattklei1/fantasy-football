"""Roster Strength: a forward-looking "how good is this roster right now"
metric, distinct from Power Score (which is backward-looking, based on
results already produced). v1 blends two ESPN-only signals per the
2026-09-13 design discussion - FantasyPros/Yahoo were considered but
FantasyPros' Terms of Use explicitly prohibit automated reproduction of
their rankings without a licensed API, and Yahoo has no generic
rest-of-season-rankings endpoint outside of league-specific OAuth access,
so neither is wired up. Revisit if/when a licensed FantasyPros API key
is obtained.

Signals (weights carried over proportionally from the original 4-source
design: ESPN weekly projection was 20/100, ESPN season rank 15/100):
- weekly_projection: this week's ESPN pre-game point projection,
  percentile-ranked WITHIN POSITION across all rostered players
  league-wide that week (comparing a projected QB score to a projected
  TE score directly would be meaningless).
- season_rank: ESPN's positional rank (`posRank` from `player_info()`),
  converted to a bounded 0-1 score via a smooth decay - not a percentile,
  since we don't reliably know the total ranked population size.

Starter vs. bench weighting decays over the season to reflect bye-week
insurance value fading (NFL byes run roughly weeks 5-14), but keeps a
floor since injury-replacement value never goes away - see
bench_weight_for_week().
"""
from __future__ import annotations

import pandas as pd

# Proportional to the original 4-source weights (ESPN weekly=20, ESPN rank=15)
SIGNAL_WEIGHTS = {
    "weekly_projection": 20 / 35,
    "season_rank": 15 / 35,
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
    projected_points, pos_rank. Adds projection_percentile, rank_score,
    and the blended player_value (0-1)."""
    df = roster_df.copy()
    df["projection_percentile"] = df.groupby("position")["projected_points"].rank(pct=True)
    df["rank_score"] = df["pos_rank"].apply(rank_to_score)

    def blend(row):
        proj, rank = row["projection_percentile"], row["rank_score"]
        has_proj, has_rank = pd.notna(proj), pd.notna(rank)
        if has_proj and has_rank:
            return SIGNAL_WEIGHTS["weekly_projection"] * proj + SIGNAL_WEIGHTS["season_rank"] * rank
        if has_proj:
            return proj
        if has_rank:
            return rank
        return 0.0

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
