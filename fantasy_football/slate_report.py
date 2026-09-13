"""Live mid-Sunday GroupMe update: cumulative scores as of whenever this
runs (see scripts/post_slate_update.py), highlighting the closest
matchups, the top individual scorer so far, the biggest current blowout,
which team(s) would be winning if their bench had started instead, and
(for this league's real top-half scoring bonus) who's on the right/wrong
side of the median cutline right now.

"Closest" and the median cutline are judged by PROJECTED FINAL totals,
not the raw score-so-far (user-reported real problem, 2026-09-13: "a lot
of times people have big leads because noone on the other team has
played" - a 60-10 score-so-far margin can be a near-toss-up once the
trailing team's players who haven't kicked off yet are accounted for,
and vice versa). `home_projected`/`away_projected` come straight from
ESPN's own live BoxScore field - confirmed elsewhere in this project
(see Matchups page) that this is a single number that IS the pre-game
projection before kickoff and live-updates to (points scored so far +
rest-of-lineup projection) once games start, so it's already the right
"how is this actually going to end up" signal with no extra computation
needed here. "Biggest blowout" and "bench would be winning" are
deliberately judged by the RAW score-so-far instead - those are about
what's actually happening on the scoreboard RIGHT NOW (the comedy/drama
of an early rout, or a bench outscoring a losing starting lineup so far),
not a rest-of-week projection.

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
deployed app's local database. One box_scores() call is fetched by the
caller and shared across every section here, rather than each section
making its own live call - matches the project's "don't over-query ESPN"
principle and keeps every section looking at the exact same instant.
"""
from __future__ import annotations

from dataclasses import dataclass

BENCH_SLOT = "BE"


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


def fetch_box_scores(league, week: int):
    """The one live ESPN call every section in this module derives from -
    see module docstring for why this is fetched once and shared."""
    return league.box_scores(week)


def build_matchup_snapshots(box_scores) -> list[MatchupSnapshot]:
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


def top_individual_scores(box_scores, limit: int = 3) -> list[tuple[str, str, float]]:
    """(player_name, team_name, points) for the highest-scoring STARTERS
    league-wide so far this week, across both rosters of every matchup."""
    rows = []
    for bs in box_scores:
        for lineup, team in ((bs.home_lineup, bs.home_team), (bs.away_lineup, bs.away_team)):
            if team is None:
                continue
            team_name = getattr(team, "team_name", str(team))
            for player in lineup:
                if getattr(player, "slot_position", None) in (BENCH_SLOT, "IR"):
                    continue
                rows.append((player.name, team_name, player.points or 0.0))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:limit]


def biggest_blowouts(matchups: list[MatchupSnapshot], limit: int = 3) -> list[MatchupSnapshot]:
    """Sorted by RAW current margin (not projected) - this is about the
    scoreboard right now, the mirror image of "closest games"."""
    return sorted(matchups, key=lambda m: m.margin, reverse=True)[:limit]


def bench_would_be_winning(box_scores) -> list[dict]:
    """Every team currently LOSING whose bench total, added to their
    actual score, would flip the result - a live version of the
    Weekly Recap's "Coaching Disaster," using score-so-far (not
    projections) since this is about what's actually on the board."""
    results = []
    for bs in box_scores:
        if bs.home_team is None or bs.away_team is None:
            continue
        for team, own_score, opp_score, lineup in (
            (bs.home_team, bs.home_score or 0.0, bs.away_score or 0.0, bs.home_lineup),
            (bs.away_team, bs.away_score or 0.0, bs.home_score or 0.0, bs.away_lineup),
        ):
            if own_score >= opp_score:
                continue  # only teams currently losing are interesting here
            bench_points = sum(p.points or 0.0 for p in lineup if getattr(p, "slot_position", None) == BENCH_SLOT)
            if own_score + bench_points > opp_score:
                results.append(
                    {
                        "team": getattr(team, "team_name", str(team)),
                        "score": own_score,
                        "opp_score": opp_score,
                        "bench_points": bench_points,
                    }
                )
    results.sort(key=lambda r: (r["score"] + r["bench_points"]) - r["opp_score"], reverse=True)
    return results


def median_cutline(league, box_scores) -> dict | None:
    """This league's real top-half-of-the-league scoring bonus, mirrored
    exactly from the Matchups page's own Median Cutline panel (same rank-
    6/7/8-of-12 convention, same "rank by current PROJECTED total"
    reasoning). Returns None when this season doesn't use the format
    (`league.settings.median_scoring`) or fewer than 8 teams have live
    box scores yet (mirrors the Matchups page's own guard)."""
    if not getattr(league.settings, "median_scoring", False):
        return None
    ranked = []
    for bs in box_scores:
        for team, projected in ((bs.home_team, bs.home_projected), (bs.away_team, bs.away_projected)):
            if team is None:
                continue
            ranked.append((getattr(team, "team_name", str(team)), projected or 0.0))
    ranked.sort(key=lambda t: t[1], reverse=True)
    if len(ranked) < 8:
        return None
    sixth, seventh, eighth = ranked[5], ranked[6], ranked[7]
    return {"making_it": sixth, "missing_it": seventh, "also_missing_it": eighth}


def build_message(
    label: str,
    matchups: list[MatchupSnapshot],
    top_scores: list[tuple[str, str, float]],
    bench_flips: list[dict] | None = None,
    cutline: dict | None = None,
) -> str:
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

    blowouts = biggest_blowouts(matchups, limit=1)
    if blowouts:
        b = blowouts[0]
        leader, trailer = (b.home_team, b.away_team) if b.home_score >= b.away_score else (b.away_team, b.home_team)
        lines.append("BIGGEST BLOWOUT RIGHT NOW:")
        lines.append(f"- {b} - {leader} is running away with it, {trailer} down {b.margin:.1f}")
        lines.append("")

    if top_scores:
        lines.append("TOP SCORES SO FAR:")
        for name, team, points in top_scores:
            lines.append(f"- {name} ({team}): {points:.1f}")
        lines.append("")

    if bench_flips:
        lines.append("BENCH WOULD BE WINNING:")
        for r in bench_flips[:3]:
            lines.append(
                f"- {r['team']} is losing {r['score']:.1f}-{r['opp_score']:.1f}, but their bench alone "
                f"scored {r['bench_points']:.1f} - the right lineup wins this one"
            )
        lines.append("")

    if cutline:
        making, missing, also_missing = cutline["making_it"], cutline["missing_it"], cutline["also_missing_it"]
        lines.append("ON THE BUBBLE (median bonus cutline, projected):")
        lines.append(f"- IN: {making[0]} ({making[1]:.1f} proj)")
        lines.append(f"- OUT: {missing[0]} ({missing[1]:.1f} proj, {making[1] - missing[1]:.1f} back)")
        lines.append(f"- OUT: {also_missing[0]} ({also_missing[1]:.1f} proj, {making[1] - also_missing[1]:.1f} back)")

    return "\n".join(lines).strip()
