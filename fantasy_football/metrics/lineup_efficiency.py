"""Manager (lineup) efficiency: Actual vs. Optimal starter points, and
whether a different legal lineup would have won the matchup. Season-to-
date cumulative through each week, same convention as season_metrics.py.
"""
from __future__ import annotations

import pandas as pd

from .lineup_optimizer import RosterPlayer, optimal_lineup
from .season_metrics import compute_matchup_results


def compute_weekly_lineup_values(roster_df: pd.DataFrame, position_slot_counts: dict) -> pd.DataFrame:
    """roster_df: one row per team-week-player - week, team_pk, player_id,
    points, is_starter, eligible_slots (a frozenset/set of slot names).
    Returns one row per team-week: actual_starter_points,
    optimal_starter_points."""
    rows = []
    for (week, team_pk), g in roster_df.groupby(["week", "team_pk"]):
        actual_starter_points = float(g.loc[g["is_starter"] == 1, "points"].sum())
        players = [
            RosterPlayer(row.player_id, row.points, row.eligible_slots) for row in g.itertuples()
        ]
        optimal_points, _assignment = optimal_lineup(players, position_slot_counts)
        rows.append(
            {
                "week": week,
                "team_pk": team_pk,
                "actual_starter_points": actual_starter_points,
                "optimal_starter_points": optimal_points,
            }
        )
    return pd.DataFrame(rows, columns=["week", "team_pk", "actual_starter_points", "optimal_starter_points"])


def compute_lineup_efficiency(
    roster_df: pd.DataFrame, matchups_df: pd.DataFrame, position_slot_counts: dict
) -> pd.DataFrame:
    """Full season-to-date snapshot per team per week - actual/optimal
    starter points, lineup efficiency, points left on bench, an
    optimal-lineup win/loss/tie record (optimal points vs. the
    opponent's REAL actual score that week), and manager-caused losses
    (weeks the optimal lineup would have won but the actual one didn't)."""
    weekly = compute_weekly_lineup_values(roster_df, position_slot_counts)
    if weekly.empty:
        return weekly

    matchup_df = compute_matchup_results(matchups_df)[
        ["week", "team_pk", "matchup_win", "points_against"]
    ]
    df = weekly.merge(matchup_df, on=["week", "team_pk"], how="left")

    df["optimal_win"] = (df["optimal_starter_points"] > df["points_against"]).astype("Int64")
    df["optimal_loss"] = (df["optimal_starter_points"] < df["points_against"]).astype("Int64")
    df["optimal_tie"] = (df["optimal_starter_points"] == df["points_against"]).astype("Int64")
    # a manager-caused loss: the optimal lineup would have won, but the real one didn't
    df["manager_caused_loss"] = ((df["optimal_win"] == 1) & (df["matchup_win"] != 1)).astype(int)

    df = df.sort_values(["team_pk", "week"]).reset_index(drop=True)
    grp = df.groupby("team_pk")

    df["actual_starter_points_cum"] = grp["actual_starter_points"].cumsum()
    df["optimal_starter_points_cum"] = grp["optimal_starter_points"].cumsum()
    df["points_left_on_bench"] = df["optimal_starter_points_cum"] - df["actual_starter_points_cum"]
    df["lineup_efficiency"] = df["actual_starter_points_cum"] / df["optimal_starter_points_cum"].replace(0, pd.NA)

    df["optimal_wins"] = grp["optimal_win"].cumsum()
    df["optimal_losses"] = grp["optimal_loss"].cumsum()
    df["optimal_ties"] = grp["optimal_tie"].cumsum()
    df["manager_caused_losses"] = grp["manager_caused_loss"].cumsum()

    return df[
        [
            "week", "team_pk",
            "actual_starter_points_cum", "optimal_starter_points_cum",
            "lineup_efficiency", "points_left_on_bench",
            "optimal_wins", "optimal_losses", "optimal_ties", "manager_caused_losses",
        ]
    ].rename(
        columns={
            "actual_starter_points_cum": "actual_starter_points",
            "optimal_starter_points_cum": "optimal_starter_points",
        }
    )
