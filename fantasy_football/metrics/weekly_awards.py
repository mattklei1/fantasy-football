"""Weekly awards: single-week (NOT cumulative) highlights, computed
automatically for every completed week - Manager of the Week, Highest/
Lowest Score, Biggest Blowout, Closest Game, Bad Beat, Luckiest Win,
Unluckiest Loss, Coaching Disaster, and Best/Worst Lineup Efficiency.
Pure/DB-free like the rest of metrics/ - fantasy_football/commentary.py
does the DB reads and team_pk -> name mapping, and feeds these into the
weekly recap's structured facts.

"Luckiest Win"/"Unluckiest Loss" use that week's ALL-PLAY record (how
many of the other teams that score would have beaten) rather than just
raw score - a more rigorous "how lucky were you" signal, and it reuses
season_metrics.compute_all_play directly instead of inventing a second
luck concept that could disagree with the Luck page's own definition.
"Biggest Fraud" is deliberately NOT computed here - the Luck page's
`fraud_index` is already a season-to-date signal (actual win% vs.
all-play win%), and the weekly recap's FRAUD WATCH section pulls that
existing per-week metrics_weekly snapshot rather than a redundant
one-week-only reconstruction of the same idea.
"""
from __future__ import annotations

import pandas as pd

from .lineup_efficiency import compute_weekly_lineup_values
from .season_metrics import compute_all_play, compute_matchup_results


def _matchup_award(row) -> dict:
    return {
        "home_team_pk": int(row["home_team_pk"]),
        "away_team_pk": int(row["away_team_pk"]),
        "home_score": float(row["home_score"]),
        "away_score": float(row["away_score"]),
        "margin": float(row["margin"]),
    }


def compute_weekly_awards(
    scores_df: pd.DataFrame,
    matchups_df: pd.DataFrame,
    roster_df: pd.DataFrame | None = None,
    position_slot_counts: dict | None = None,
) -> dict:
    """scores_df: week, team_pk, score - ONE week only (caller filters).
    matchups_df: week, home_team_pk, away_team_pk, home_score, away_score
    - the same single week. roster_df/position_slot_counts (optional,
    2019+ only - needs box-score eligibility data): week, team_pk,
    player_id, points, is_starter, eligible_slots - enables Coaching
    Disaster and Best/Worst Lineup Efficiency; omitted (None in the
    result) rather than fabricated when not available.

    Returns a dict keyed by award name -> a fact dict, or None when that
    award has no valid candidate this week (e.g. Bad Beat needs at least
    one loss - impossible in a bye-only or all-tie week)."""
    if scores_df.empty or matchups_df.empty:
        return {}

    match_results = compute_matchup_results(matchups_df)
    merged = scores_df.merge(match_results, on=["week", "team_pk"], how="left")
    all_play = compute_all_play(scores_df).drop(columns=["week"])
    merged = merged.merge(all_play, on="team_pk", how="left")

    awards: dict = {}

    highest = merged.loc[merged["score"].idxmax()]
    lowest = merged.loc[merged["score"].idxmin()]
    awards["highest_score"] = {"team_pk": int(highest["team_pk"]), "score": float(highest["score"])}
    awards["lowest_score"] = {"team_pk": int(lowest["team_pk"]), "score": float(lowest["score"])}

    m = matchups_df.copy()
    m["margin"] = (m["home_score"] - m["away_score"]).abs()
    awards["biggest_blowout"] = _matchup_award(m.loc[m["margin"].idxmax()])
    awards["closest_game"] = _matchup_award(m.loc[m["margin"].idxmin()])

    losers = merged[merged["matchup_loss"] == 1]
    winners = merged[merged["matchup_win"] == 1]

    if not losers.empty:
        bad_beat = losers.loc[losers["score"].idxmax()]
        awards["bad_beat"] = {"team_pk": int(bad_beat["team_pk"]), "score": float(bad_beat["score"])}
        unluckiest = losers.loc[losers["all_play_win"].idxmax()]
        awards["unluckiest_loss"] = {
            "team_pk": int(unluckiest["team_pk"]),
            "score": float(unluckiest["score"]),
            "all_play_wins": int(unluckiest["all_play_win"]),
        }
    else:
        awards["bad_beat"] = None
        awards["unluckiest_loss"] = None

    if not winners.empty:
        luckiest = winners.loc[winners["all_play_win"].idxmin()]
        awards["luckiest_win"] = {
            "team_pk": int(luckiest["team_pk"]),
            "score": float(luckiest["score"]),
            "all_play_wins": int(luckiest["all_play_win"]),
        }
        # Manager of the Week: among teams that WON their matchup, the
        # highest score - a decisive "best week" (won AND outscored the
        # rest of the winners), not just the single highest score
        # league-wide (that's the separate Highest Score award above).
        mow = winners.loc[winners["score"].idxmax()]
        awards["manager_of_the_week"] = {"team_pk": int(mow["team_pk"]), "score": float(mow["score"])}
    else:
        awards["luckiest_win"] = None
        awards["manager_of_the_week"] = None

    if roster_df is not None and not roster_df.empty and position_slot_counts:
        weekly_lineup = compute_weekly_lineup_values(roster_df, position_slot_counts)
        weekly_lineup = weekly_lineup.merge(
            merged[["team_pk", "matchup_loss", "points_against"]], on="team_pk", how="left"
        )
        weekly_lineup["points_left_on_bench"] = (
            weekly_lineup["optimal_starter_points"] - weekly_lineup["actual_starter_points"]
        )
        weekly_lineup["lineup_efficiency"] = weekly_lineup["actual_starter_points"] / weekly_lineup[
            "optimal_starter_points"
        ].replace(0, pd.NA)

        best_eff = weekly_lineup.loc[weekly_lineup["lineup_efficiency"].idxmax()]
        worst_eff = weekly_lineup.loc[weekly_lineup["lineup_efficiency"].idxmin()]
        awards["best_lineup_efficiency"] = {
            "team_pk": int(best_eff["team_pk"]),
            "lineup_efficiency": float(best_eff["lineup_efficiency"]),
        }
        awards["worst_lineup_efficiency"] = {
            "team_pk": int(worst_eff["team_pk"]),
            "lineup_efficiency": float(worst_eff["lineup_efficiency"]),
        }

        # Coaching Disaster: among THIS WEEK's losses, the optimal lineup
        # would have beaten the opponent's real score - a loss the lineup
        # decision caused, not bad luck. Picks the biggest points left on
        # bench among those. Falls back to the single biggest points-left-
        # on-bench of the week (regardless of outcome) if no lineup
        # mistake actually flipped a result this week - still a real,
        # documented stat, not a fabricated "disaster."
        lost = weekly_lineup[weekly_lineup["matchup_loss"] == 1].copy()
        flipped = lost[
            (lost["points_left_on_bench"] > 0) & (lost["optimal_starter_points"] > lost["points_against"])
        ]
        pool = flipped if not flipped.empty else weekly_lineup
        disaster = pool.loc[pool["points_left_on_bench"].idxmax()]
        awards["coaching_disaster"] = {
            "team_pk": int(disaster["team_pk"]),
            "points_left_on_bench": float(disaster["points_left_on_bench"]),
            "flipped_result": bool(not flipped.empty),
        }
    else:
        awards["best_lineup_efficiency"] = None
        awards["worst_lineup_efficiency"] = None
        awards["coaching_disaster"] = None

    return awards
