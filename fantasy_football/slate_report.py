"""Live mid-Sunday GroupMe update: cumulative scores as of whenever this
runs (see scripts/post_slate_update.py), highlighting the closest
matchups and the top individual scorer so far.

Scope decision, stated plainly rather than silently assumed: this reports
whatever has scored so far LEAGUE-WIDE at trigger time, not scores
isolated to "only players whose NFL games are in the early window." Doing
that precisely would need each player's actual NFL game time cross-
referenced against the roster, which is a lot of extra complexity for a
mid-day text message - a well-chosen trigger time (e.g. ~1:30pm ET for
the early slate, ~4:30pm ET for the afternoon slate) makes the simple
cumulative-score version a good enough proxy. Revisit only if the
timing ever actually feels off in practice.

Does its own live ESPN call, same reasoning as waiver_report.py - this
runs from GitHub Actions, a separate environment with no access to the
deployed app's local database.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MatchupSnapshot:
    home_team: str
    home_score: float
    away_team: str
    away_score: float

    @property
    def margin(self) -> float:
        return abs(self.home_score - self.away_score)

    def __str__(self) -> str:
        return f"{self.home_team} {self.home_score:.1f} - {self.away_score:.1f} {self.away_team}"


def fetch_live_matchups(league, week: int) -> list[MatchupSnapshot]:
    box_scores = league.box_scores(week)
    snapshots = []
    for bs in box_scores:
        if bs.home_team is None or bs.away_team is None:
            continue  # a playoff bye - no real second team to compare
        home_name = getattr(bs.home_team, "team_name", str(bs.home_team))
        away_name = getattr(bs.away_team, "team_name", str(bs.away_team))
        snapshots.append(MatchupSnapshot(home_name, bs.home_score or 0.0, away_name, bs.away_score or 0.0))
    return snapshots


def top_individual_scores(league, week: int, limit: int = 3) -> list[tuple[str, str, float]]:
    """(player_name, team_name, points) for the highest-scoring STARTERS
    league-wide so far this week, across both rosters of every matchup."""
    box_scores = league.box_scores(week)
    rows = []
    for bs in box_scores:
        for lineup, team in ((bs.home_lineup, bs.home_team), (bs.away_lineup, bs.away_team)):
            if team is None:
                continue
            team_name = getattr(team, "team_name", str(team))
            for player in lineup:
                if getattr(player, "slot_position", None) in ("BE", "IR"):
                    continue
                rows.append((player.name, team_name, player.points or 0.0))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:limit]


def build_message(label: str, matchups: list[MatchupSnapshot], top_scores: list[tuple[str, str, float]]) -> str:
    if not matchups:
        return f"{label}: no games in progress yet."

    lines = [f"{label.upper()}", ""]

    closest = sorted(matchups, key=lambda m: m.margin)[:3]
    lines.append("CLOSEST GAMES RIGHT NOW:")
    for m in closest:
        lines.append(f"- {m}")
    lines.append("")

    if top_scores:
        lines.append("TOP SCORES SO FAR:")
        for name, team, points in top_scores:
            lines.append(f"- {name} ({team}): {points:.1f}")

    return "\n".join(lines).strip()
