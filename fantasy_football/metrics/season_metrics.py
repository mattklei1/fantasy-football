"""Deterministic, pandas-only metric calculations: All-Play record, Luck,
Fraud Index, Power Score. No ESPN calls and no LLM calls happen here -
everything is pure computation over already-ingested data, which is what
makes it testable with small synthetic DataFrames (see tests/test_metrics.py)
and safe to hand structured results to Claude for commentary later without
Claude ever touching the arithmetic itself.

All output is a SEASON-TO-DATE snapshot through each week (so storing every
week's row gives rank-movement history for free) - see fantasy_football/db.py
metrics_weekly for the exact column meanings and the matchup-vs-median-vs-
all-play design rationale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POWER_WEIGHTS = {
    "ppg_percentile": 0.35,
    "all_play_win_pct": 0.30,
    "recent_form_percentile": 0.20,
    "actual_win_pct": 0.15,
}


def compute_matchup_results(matchups_df: pd.DataFrame) -> pd.DataFrame:
    """Unpivot home/away matchup rows into one row per team per week:
    week, team_pk, matchup_win/loss/tie (0/1), points_against (opponent's
    score that week)."""
    cols = ["week", "team_pk", "matchup_win", "matchup_loss", "matchup_tie", "points_against"]
    if matchups_df.empty:
        return pd.DataFrame(columns=cols)

    home = matchups_df.rename(
        columns={"home_team_pk": "team_pk", "home_score": "own_score", "away_score": "opp_score"}
    )[["week", "team_pk", "own_score", "opp_score"]]
    away = matchups_df.rename(
        columns={"away_team_pk": "team_pk", "away_score": "own_score", "home_score": "opp_score"}
    )[["week", "team_pk", "own_score", "opp_score"]]
    both = pd.concat([home, away], ignore_index=True)

    both["matchup_win"] = (both["own_score"] > both["opp_score"]).astype(int)
    both["matchup_loss"] = (both["own_score"] < both["opp_score"]).astype(int)
    both["matchup_tie"] = (both["own_score"] == both["opp_score"]).astype(int)
    both["points_against"] = both["opp_score"]
    return both[cols]


def compute_median_results(scores_df: pd.DataFrame) -> pd.DataFrame:
    """ESPN's top-half/bottom-half ("median game") result per team per
    week: compare each team's score to that week's median across ALL
    teams (self included). Above median = win, below = loss, exactly the
    median = tie. Computed for every season regardless of whether that
    season's rules counted it - see db.py comment on metrics_weekly."""
    df = scores_df.copy()
    df["week_median"] = df.groupby("week")["score"].transform("median")
    df["median_win"] = (df["score"] > df["week_median"]).astype(int)
    df["median_loss"] = (df["score"] < df["week_median"]).astype(int)
    df["median_tie"] = (df["score"] == df["week_median"]).astype(int)
    return df[["week", "team_pk", "median_win", "median_loss", "median_tie"]]


def compute_all_play(scores_df: pd.DataFrame) -> pd.DataFrame:
    """For every week, each team's record if it had played every other
    team that scored that week (n-1 comparisons)."""
    rows = []
    for week, g in scores_df.groupby("week"):
        scores = g["score"].to_numpy()
        for team_pk, score in zip(g["team_pk"], scores):
            wins = int((scores < score).sum())
            losses = int((scores > score).sum())
            ties = int((scores == score).sum()) - 1  # exclude the team's own row
            rows.append(
                {"week": week, "team_pk": team_pk, "all_play_win": wins, "all_play_loss": losses, "all_play_tie": ties}
            )
    return pd.DataFrame(rows, columns=["week", "team_pk", "all_play_win", "all_play_loss", "all_play_tie"])


def _safe_div(numer: pd.Series, denom: pd.Series) -> pd.Series:
    return numer / denom.replace(0, np.nan)


def compute_season_metrics(
    scores_df: pd.DataFrame, matchups_df: pd.DataFrame, median_scoring: bool
) -> pd.DataFrame:
    """Full season-to-date snapshot per team per week. `scores_df` must
    have one row per team per completed week (week, team_pk, score);
    `matchups_df` one row per completed real matchup that week."""
    if scores_df.empty:
        return pd.DataFrame()

    matchup_df = compute_matchup_results(matchups_df)
    median_df = compute_median_results(scores_df)
    all_play_df = compute_all_play(scores_df)

    df = scores_df.merge(matchup_df, on=["week", "team_pk"], how="left")
    df = df.merge(median_df, on=["week", "team_pk"], how="left")
    df = df.merge(all_play_df, on=["week", "team_pk"], how="left")
    df = df.sort_values(["team_pk", "week"]).reset_index(drop=True)

    grp = df.groupby("team_pk")
    df["matchup_wins"] = grp["matchup_win"].cumsum()
    df["matchup_losses"] = grp["matchup_loss"].cumsum()
    df["matchup_ties"] = grp["matchup_tie"].cumsum()
    df["median_wins"] = grp["median_win"].cumsum()
    df["median_losses"] = grp["median_loss"].cumsum()
    df["median_ties"] = grp["median_tie"].cumsum()
    df["all_play_wins"] = grp["all_play_win"].cumsum()
    df["all_play_losses"] = grp["all_play_loss"].cumsum()
    df["all_play_ties"] = grp["all_play_tie"].cumsum()

    df["games_played"] = grp.cumcount() + 1
    df["points_for"] = grp["score"].cumsum()
    # a bye week (rare, 2015-2020 playoff format) has no real opponent -
    # points_against contributes 0 for that week only, a known minor gap
    df["points_against"] = df["points_against"].fillna(0)
    df["points_against"] = df.groupby("team_pk")["points_against"].cumsum()
    df["ppg"] = df["points_for"] / df["games_played"]
    df["last3_ppg"] = grp["score"].transform(lambda s: s.rolling(3, min_periods=1).mean())

    # percentiles are computed WITHIN each week, across that week's field -
    # this is what keeps Power Score meaningful season-to-season even as
    # scoring rules/roster settings change (see PROJECT_BRIEF cross-season
    # normalization note): a team is only ever compared to its own season.
    df["ppg_percentile"] = df.groupby("week")["ppg"].rank(pct=True)
    df["recent_form_percentile"] = df.groupby("week")["last3_ppg"].rank(pct=True)

    all_play_total = df["all_play_wins"] + df["all_play_losses"] + df["all_play_ties"]
    df["all_play_win_pct"] = _safe_div(df["all_play_wins"] + 0.5 * df["all_play_ties"], all_play_total)

    matchup_win_equiv = df["matchup_wins"] + 0.5 * df["matchup_ties"]
    matchup_win_pct = _safe_div(matchup_win_equiv, df["games_played"])

    if median_scoring:
        median_win_equiv = df["median_wins"] + 0.5 * df["median_ties"]
        combined_win_equiv = matchup_win_equiv + median_win_equiv
        combined_decisions = df["games_played"] * 2
        df["actual_win_pct"] = _safe_div(combined_win_equiv, combined_decisions)
    else:
        df["actual_win_pct"] = matchup_win_pct

    # Luck Wins deliberately uses the MATCHUP-only record (not the
    # combined record) against All-Play expected wins - it isolates pure
    # opponent-schedule luck. The median bonus isn't opponent-dependent,
    # so it doesn't belong in a "did your schedule help/hurt you" metric.
    df["expected_wins"] = df["all_play_win_pct"] * df["games_played"]
    df["luck_wins"] = matchup_win_equiv - df["expected_wins"]

    # Fraud Index uses the REAL displayed record (combined, when
    # applicable) since that's what determines standings/seeding and what
    # the badge is actually calling out as fraudulent.
    df["fraud_index"] = df["actual_win_pct"] - df["all_play_win_pct"]

    df["power_score"] = 100 * (
        POWER_WEIGHTS["ppg_percentile"] * df["ppg_percentile"].fillna(0)
        + POWER_WEIGHTS["all_play_win_pct"] * df["all_play_win_pct"].fillna(0)
        + POWER_WEIGHTS["recent_form_percentile"] * df["recent_form_percentile"].fillna(0)
        + POWER_WEIGHTS["actual_win_pct"] * df["actual_win_pct"].fillna(0)
    )

    return df
