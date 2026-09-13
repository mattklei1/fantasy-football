"""History / Hall of Fame / head-to-head calculations - pure, DB-free,
testable with small mock DataFrames per the project's testing convention.

Cross-season note: league records below (highest/lowest score, biggest
blowout, etc.) are RAW facts spanning every season, including years with
different scoring rules - that's appropriate for a record book (a
historical record is a historical record, era and all), but it means
these are NOT a fair basis for ranking managers against each other. For
manager comparisons (Hall of Fame "best season"), use season-relative
percentile measures instead (already computed in metrics_weekly.
ppg_percentile) - see history_data.py, which is where that distinction
gets applied.
"""
from __future__ import annotations

import pandas as pd

#: The ONLY matchup_type values that represent a real playoff berth. ESPN
#: flags every post-regular-season game as is_playoff=1, including
#: LOSERS_CONSOLATION_LADDER - the placement bracket for teams that did NOT
#: make the playoffs (the "toilet bowl"). WINNERS_BRACKET is the real
#: championship bracket; WINNERS_CONSOLATION_LADDER is the placement
#: bracket for teams that DID qualify but lost early - still real playoff
#: participants. Verified against every season's real playoff_team_count
#: (2026-09-13): the distinct-team count under these two values matches
#: playoff_team_count exactly for every completed season in this league.
REAL_PLAYOFF_MATCHUP_TYPES = ("WINNERS_BRACKET", "WINNERS_CONSOLATION_LADDER")


def compute_streaks(results: pd.Series) -> dict:
    """results: chronologically ordered sequence of 'W'/'L'/'T'.
    Returns longest win streak, longest loss streak, and the current
    (most recent) streak as (result, length)."""
    longest_win = longest_loss = 0
    current_run = 0
    current_result = None
    for r in results:
        if r == current_result:
            current_run += 1
        else:
            current_result = r
            current_run = 1
        if current_result == "W":
            longest_win = max(longest_win, current_run)
        elif current_result == "L":
            longest_loss = max(longest_loss, current_run)

    current_streak = (None, 0)
    if len(results) > 0:
        last = results.iloc[-1]
        n = 0
        for r in reversed(results.tolist()):
            if r == last:
                n += 1
            else:
                break
        current_streak = (last, n)

    return {
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak_result": current_streak[0],
        "current_streak_length": current_streak[1],
    }


def _result(own_score: float, opp_score: float) -> str:
    if own_score > opp_score:
        return "W"
    if own_score < opp_score:
        return "L"
    return "T"


def compute_head_to_head(matchups: pd.DataFrame, manager_a: str, manager_b: str) -> dict:
    """matchups: one row per completed matchup with columns season_id,
    week, is_playoff, matchup_type, home_manager_id, away_manager_id,
    home_score, away_score (already resolved to manager id, not team_pk -
    a manager's team changes identity each season, but the manager
    doesn't). Returns a summary dict plus the full chronological history.

    The overall All-Time Series (a_wins/b_wins) counts every game these
    two ever played, playoffs and consolation-ladder placement games
    alike - a complete history. The separate Playoff Record breakdown
    below is scoped to REAL_PLAYOFF_MATCHUP_TYPES only - ESPN's
    consolation ladder for teams that MISSED the playoffs isn't a
    meaningful "playoff record" even though ESPN tags it is_playoff=1."""
    mask = (
        (matchups["home_manager_id"] == manager_a) & (matchups["away_manager_id"] == manager_b)
    ) | ((matchups["home_manager_id"] == manager_b) & (matchups["away_manager_id"] == manager_a))
    games = matchups[mask].sort_values(["season_id", "week"]).copy()

    if games.empty:
        return {"games_played": 0, "history": games}

    def a_score(row):
        return row["home_score"] if row["home_manager_id"] == manager_a else row["away_score"]

    def b_score(row):
        return row["home_score"] if row["home_manager_id"] == manager_b else row["away_score"]

    games["a_score"] = games.apply(a_score, axis=1)
    games["b_score"] = games.apply(b_score, axis=1)
    games["margin"] = (games["a_score"] - games["b_score"]).abs()
    games["a_result"] = games.apply(lambda r: _result(r["a_score"], r["b_score"]), axis=1)

    a_wins = int((games["a_result"] == "W").sum())
    b_wins = int((games["a_result"] == "L").sum())
    ties = int((games["a_result"] == "T").sum())

    playoff_games = games[games["matchup_type"].isin(REAL_PLAYOFF_MATCHUP_TYPES)]
    a_playoff_wins = int((playoff_games["a_result"] == "W").sum())
    b_playoff_wins = int((playoff_games["a_result"] == "L").sum())

    largest_margin_row = games.loc[games["margin"].idxmax()]
    closest_row = games.loc[games["margin"].idxmin()]

    streaks = compute_streaks(games["a_result"])

    return {
        "games_played": len(games),
        "a_wins": a_wins,
        "b_wins": b_wins,
        "ties": ties,
        "a_total_points": float(games["a_score"].sum()),
        "b_total_points": float(games["b_score"].sum()),
        "a_avg_points": float(games["a_score"].mean()),
        "b_avg_points": float(games["b_score"].mean()),
        "playoff_games": len(playoff_games),
        "a_playoff_wins": a_playoff_wins,
        "b_playoff_wins": b_playoff_wins,
        "largest_margin": float(largest_margin_row["margin"]),
        "largest_margin_winner": manager_a if largest_margin_row["a_result"] == "W" else manager_b,
        "largest_margin_season": int(largest_margin_row["season_id"]),
        "largest_margin_week": int(largest_margin_row["week"]),
        "closest_margin": float(closest_row["margin"]),
        "closest_season": int(closest_row["season_id"]),
        "closest_week": int(closest_row["week"]),
        "current_streak_manager": (
            manager_a if streaks["current_streak_result"] == "W"
            else manager_b if streaks["current_streak_result"] == "L"
            else None
        ),
        "current_streak_length": streaks["current_streak_length"],
        "history": games[
            ["season_id", "week", "is_playoff", "a_score", "b_score", "a_result", "margin"]
        ],
    }


def compute_league_records(matchups: pd.DataFrame) -> dict:
    """matchups: one row per completed matchup, both sides unpivoted into
    team-week rows with columns season_id, week, team_pk, team_name,
    manager_name, score, opp_score, opp_team_name, opp_manager_name,
    is_playoff, and (optional) score_percentile - that last one is a
    season-relative rank percentile computed by history_data.py (kept
    out of this pure/DB-free module on purpose). When present, Highest/
    Lowest Score Ever are picked by score_percentile (how exceptional a
    score was WITHIN its own season, fair across eras with different
    scoring settings) rather than raw points - raw points still get
    shown as the headline number, since that's the fact people actually
    remember, with the percentile as supporting context."""
    if matchups.empty:
        return {}

    m = matchups.copy()
    m["margin"] = m["score"] - m["opp_score"]
    rank_col = "score_percentile" if "score_percentile" in m.columns else "score"

    highest = m.loc[m[rank_col].idxmax()]
    lowest = m.loc[m[rank_col].idxmin()]
    blowout = m.loc[m["margin"].idxmax()]
    closest = m.loc[m["margin"].abs().idxmin()]

    losses = m[m["margin"] < 0]
    wins = m[m["margin"] > 0]
    most_in_loss = losses.loc[losses["score"].idxmax()] if not losses.empty else None
    lowest_in_win = wins.loc[wins["score"].idxmin()] if not wins.empty else None

    def row_dict(row, with_opponent: bool = False):
        if row is None:
            return None
        d = {
            "team_name": row["team_name"], "manager_name": row.get("manager_name"),
            "season_id": int(row["season_id"]), "week": int(row["week"]),
            "score": float(row["score"]),
        }
        if "score_percentile" in row.index and pd.notna(row["score_percentile"]):
            d["score_percentile"] = float(row["score_percentile"])
        if with_opponent:
            d["opp_team_name"] = row.get("opp_team_name")
            d["opp_manager_name"] = row.get("opp_manager_name")
            d["opp_score"] = float(row["opp_score"])
        return d

    return {
        "highest_score": row_dict(highest),
        "lowest_score": row_dict(lowest),
        "biggest_blowout": {**row_dict(blowout, with_opponent=True), "margin": abs(float(blowout["margin"]))},
        "closest_game": {**row_dict(closest, with_opponent=True), "margin": abs(float(closest["margin"]))},
        "most_points_in_loss": row_dict(most_in_loss),
        "lowest_score_in_win": row_dict(lowest_in_win),
    }
