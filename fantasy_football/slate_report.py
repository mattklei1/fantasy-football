"""Live mid-Sunday GroupMe update: cumulative scores as of whenever this
runs (see scripts/post_slate_update.py), highlighting the closest and
most lopsided matchups by projected outcome, the top individual scorer
so far, the single worst lineup decision on the board right now, and
(for this league's real top-half scoring bonus) the full 12-team median
cutline so everyone can see exactly how close they are.

"Closest games" and "biggest blowout" are BOTH judged by PROJECTED FINAL
margin, not the raw score-so-far (user-reported real problem,
2026-09-13/14: "a lot of times people have big leads because noone on
the other team has played" - a 60-10 score-so-far margin can be a
near-toss-up once the trailing team's players who haven't kicked off yet
are accounted for, and a modest current lead can be a real rout once
projections are in. `home_projected`/`away_projected` come straight from
ESPN's own live BoxScore field - confirmed elsewhere in this project
(see Matchups page) that this is a single number that IS the pre-game
projection before kickoff and live-updates to (points scored so far +
rest-of-lineup projection) once games start, so it's already the right
"how is this actually going to end up" signal with no extra computation
needed here.

"Worst lineup decision" (bench_would_be_winning) uses the EXACT same
Hungarian-algorithm optimal-lineup solver (metrics/lineup_optimizer.py)
Lineup Efficiency uses for real weekly points, fed every rostered
player's real points + REAL slot eligibility - not a naive "sum every
bench player's points" heuristic, which would illegally let e.g. a bench
D/ST's points substitute for a flex spot. Only the single worst offender
is surfaced (the team currently losing whose optimal lineup would have
scored enough to flip the result, by the largest actual-vs-optimal gap)
- user feedback 2026-09-14: this should call out one real screwup, not a
list of up-to-three maybes.

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

from .metrics.lineup_optimizer import RosterPlayer, optimal_lineup

BENCH_SLOT = "BE"
IR_SLOT = "IR"


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
                if getattr(player, "slot_position", None) in (BENCH_SLOT, IR_SLOT):
                    continue
                rows.append((player.name, team_name, player.points or 0.0))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows[:limit]


def biggest_blowouts(matchups: list[MatchupSnapshot], limit: int = 3) -> list[MatchupSnapshot]:
    """Sorted by PROJECTED margin (not raw current score) - consistent
    with Closest Games and the same underlying reasoning: a big CURRENT
    gap is often just "one team hasn't played yet," not a real blowout,
    and a modest current gap can be a real rout once projections are in.
    Flipped from raw-margin sorting per user feedback 2026-09-14."""
    return sorted(matchups, key=lambda m: m.projected_margin, reverse=True)[:limit]


def worst_lineup_decision(box_scores, position_slot_counts: dict) -> dict | None:
    """The SINGLE team that made the worst lineup decision on the board
    right now: currently losing, but whose real optimal legal lineup
    (every rostered player's actual points + actual slot eligibility, run
    through the exact same Hungarian-algorithm solver Lineup Efficiency
    uses - never a naive "sum the bench" shortcut, which would illegally
    let e.g. a bench kicker's points cover a flex spot) would have scored
    enough to beat their opponent's real score. Among every team that
    qualifies, picks the one with the largest gap between what they
    actually started and what their optimal lineup would have scored -
    the most self-inflicted loss right now. None if nobody currently
    qualifies (most weeks, most teams' actual lineup IS close to
    optimal)."""
    candidates = []
    for bs in box_scores:
        if bs.home_team is None or bs.away_team is None:
            continue
        for team, own_score, opp_score, lineup in (
            (bs.home_team, bs.home_score or 0.0, bs.away_score or 0.0, bs.home_lineup),
            (bs.away_team, bs.away_score or 0.0, bs.home_score or 0.0, bs.away_lineup),
        ):
            if own_score >= opp_score:
                continue  # only teams currently losing are interesting here
            players = [
                RosterPlayer(p.playerId, p.points or 0.0, frozenset(p.eligibleSlots))
                for p in lineup
                if getattr(p, "slot_position", None) != IR_SLOT
            ]
            optimal_score, _ = optimal_lineup(players, position_slot_counts)
            if optimal_score > opp_score:
                candidates.append(
                    {
                        "team": getattr(team, "team_name", str(team)),
                        "score": own_score,
                        "opp_score": opp_score,
                        "optimal_score": optimal_score,
                    }
                )
    if not candidates:
        return None
    candidates.sort(key=lambda c: c["optimal_score"] - c["score"], reverse=True)
    return candidates[0]


def median_cutline(league, box_scores) -> dict | None:
    """This league's real top-half-of-the-league scoring bonus - EVERY
    team ranked by current PROJECTED total (not raw score-so-far, same
    reasoning as the rest of this module), each with its real point
    distance from the cutline, so the whole league can see exactly how
    close they are, not just the 3 nearest the line (user feedback
    2026-09-14). Returns None when this season doesn't use the format
    (`league.settings.median_scoring`) or fewer than 2 teams have live
    box scores yet."""
    if not getattr(league.settings, "median_scoring", False):
        return None
    ranked = []
    for bs in box_scores:
        for team, projected in ((bs.home_team, bs.home_projected), (bs.away_team, bs.away_projected)):
            if team is None:
                continue
            ranked.append((getattr(team, "team_name", str(team)), projected or 0.0))
    ranked.sort(key=lambda t: t[1], reverse=True)
    n = len(ranked)
    if n < 2:
        return None
    cut_index = n // 2  # the cut_index-th team (1-indexed) is the last one making it
    cutline_score = ranked[cut_index - 1][1]
    teams = [
        {"rank": i + 1, "team": name, "projected": proj, "diff": proj - cutline_score, "making_it": i < cut_index}
        for i, (name, proj) in enumerate(ranked)
    ]
    return {"teams": teams, "cut_index": cut_index}


def build_message(
    label: str,
    matchups: list[MatchupSnapshot],
    top_scores: list[tuple[str, str, float]],
    worst_decision: dict | None = None,
    cutline: dict | None = None,
) -> str:
    if not matchups:
        return f"🏈 **{label}**: no games in progress yet."

    lines = [f"🏈 **{label.upper()}**", ""]

    # Sorted by PROJECTED final margin, not the raw score-so-far margin -
    # see module docstring for why (a big score-so-far gap is often just
    # "the other team hasn't played yet," not a real edge).
    closest = sorted(matchups, key=lambda m: m.projected_margin)[:3]
    lines.append("🔥 **CLOSEST GAMES (BY PROJECTED FINISH)**:")
    for m in closest:
        lines.append(f"- {m}")
    lines.append("")

    blowouts = biggest_blowouts(matchups, limit=1)
    if blowouts:
        b = blowouts[0]
        proj_leader, proj_trailer = (
            (b.home_team, b.away_team) if b.home_projected >= b.away_projected else (b.away_team, b.home_team)
        )
        lines.append("💥 **BIGGEST BLOWOUT (BY PROJECTED FINISH)**:")
        lines.append(f"- {b} - **{proj_leader}** projected to beat {proj_trailer} by **{b.projected_margin:.1f}**")
        lines.append("")

    if top_scores:
        lines.append("⭐ **TOP SCORES SO FAR**:")
        for name, team, points in top_scores:
            lines.append(f"- {name} ({team}): **{points:.1f}**")
        lines.append("")

    if worst_decision:
        r = worst_decision
        lines.append("🤦 **WORST LINEUP DECISION THIS WEEK**:")
        lines.append(
            f"- **{r['team']}** is losing {r['score']:.1f}-{r['opp_score']:.1f}, but their real optimal legal "
            f"lineup scores **{r['optimal_score']:.1f}** - enough to win this one"
        )
        lines.append("")

    if cutline:
        lines.append("📊 **ON THE BUBBLE (median bonus cutline, projected)**:")
        for t in cutline["teams"]:
            lines.append(f"{t['rank']}. {t['team']} {t['projected']:.1f} pts ({t['diff']:+.1f})")
            if t["rank"] == cutline["cut_index"]:
                lines.append("--- CUTLINE ---")

    return "\n".join(lines).strip()
