"""Live mid-Sunday GroupMe update: cumulative scores as of whenever this
runs (see scripts/post_slate_update.py), highlighting the closest
matchups and the top individual scorer so far.

"Closest" is judged by PROJECTED FINAL margin, not the raw score-so-far
margin (user-reported real problem, 2026-09-13: "a lot of times people
have big leads because noone on the other team has played" - a 60-10
score-so-far margin can be a near-toss-up once the trailing team's
players who haven't kicked off yet are accounted for, and vice versa).
`home_projected`/`away_projected` come straight from ESPN's own live
BoxScore field - confirmed elsewhere in this project (see Matchups page)
that this is a single number that IS the pre-game projection before
kickoff and live-updates to (points scored so far + rest-of-lineup
projection) once games start, so it's already the right "how is this
actually going to end up" signal with no extra computation needed here.

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
    home_projected: float = 0.0
    away_projected: float = 0.0

    @property
    def margin(self) -> float:
        return abs(self.home_score - self.away_score)

    @property
    def projected_margin(self) -> float:
        return abs(self.home_projected - self.away_projected)

    def __str__(self) -> str:
        base = f"{self.home_team} {self.home_score:.1f} - {self.away_score:.1f} {self.away_team}"
        if self.home_projected or self.away_projected:
            base += f" (proj {self.home_projected:.1f}-{self.away_projected:.1f})"
        return base


def fetch_live_matchups(league, week: int) -> list[MatchupSnapshot]:
    box_scores = league.box_scores(week)
    snapshots = []
    for bs in box_scores:
        if bs.home_team is None or bs.away_team is None:
            continue  # a playoff bye - no real second team to compare
        home_name = getattr(bs.home_team, "team_name", str(bs.home_team))
        away_name = getattr(bs.away_team, "team_name", str(bs.away_team))
        snapshots.append(
            MatchupSnapshot(
                home_name, bs.home_score or 0.0, away_name, bs.away_score or 0.0,
                home_projected=bs.home_projected or 0.0, away_projected=bs.away_projected or 0.0,
            )
        )
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

    # Sorted by PROJECTED final margin, not the raw score-so-far margin -
    # see module docstring for why (a big score-so-far gap is often just
    # "the other team hasn't played yet," not a real edge).
    closest = sorted(matchups, key=lambda m: m.projected_margin)[:3]
    lines.append("CLOSEST GAMES (BY PROJECTED FINISH):")
    for m in closest:
        lines.append(f"- {m}")
    lines.append("")

    if top_scores:
        lines.append("TOP SCORES SO FAR:")
        for name, team, points in top_scores:
            lines.append(f"- {name} ({team}): {points:.1f}")

    return "\n".join(lines).strip()
