"""Weekly waiver-wire GroupMe recap. Pulls this week's real waiver
claims directly from ESPN (including losing bids - see PROJECT_BRIEF for
how `league.transactions()` was confirmed, against this league's own
2025 history, to expose the full claim log with real bid amounts on
losing claims too, unlike the `recent_activity()` endpoint this
project's regular ingestion uses), computes a suggested-bid baseline via
metrics/waiver_value.py for context, and assembles a GroupMe-ready
message.

Does its own live ESPN + FantasyPros calls rather than reading from
data/league.db on purpose - this runs from a GitHub Actions job, a
completely separate, ephemeral environment from the deployed Streamlit
app, so it has no access to (and no dependency on) that app's local
database file anyway.

Messages mark up key phrases with **bold** and a couple of section-
header emoji - groupme_client.send_long_message converts the markup to
real Unicode bold before posting (see that module for why GroupMe itself
has no native formatting).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .metrics.waiver_value import position_scarcity_multipliers, suggested_bid

OVERSPEND_RATIO = 1.5  # flag a winning bid at >=1.5x our suggested value
OVERSPEND_MIN_GAP = 5.0  # ...and at least $5 over, so $1-vs-$2 noise doesn't get flagged
STEAL_RATIO = 0.5  # flag a winning bid at <=50% of our suggested value
STEAL_MIN_GAP = 5.0  # ...and at least $5 under, so $1-vs-$2 noise doesn't get flagged


@dataclass
class PlayerClaimResult:
    player_id: int
    player_name: str
    position: str | None = None
    winner_team: str | None = None
    winning_bid: float | None = None
    all_bids: list[tuple[str, float]] = field(default_factory=list)  # (team_name, bid) for every distinct team that tried
    suggested_bid: float | None = None

    @property
    def contested(self) -> bool:
        return len(self.all_bids) > 1

    @property
    def overspent(self) -> bool:
        if self.winning_bid is None or self.suggested_bid is None:
            return False
        return (
            self.winning_bid >= self.suggested_bid * OVERSPEND_RATIO
            and self.winning_bid - self.suggested_bid >= OVERSPEND_MIN_GAP
        )

    @property
    def steal(self) -> bool:
        if self.winning_bid is None or self.suggested_bid is None:
            return False
        return (
            self.winning_bid <= self.suggested_bid * STEAL_RATIO
            and self.suggested_bid - self.winning_bid >= STEAL_MIN_GAP
        )


def fetch_week_claims(league, scoring_period: int) -> list[PlayerClaimResult]:
    """One result per distinct player with at least one WAIVER-type claim
    this scoring period, win or lose."""
    txns = league.transactions(scoring_period=scoring_period, types={"WAIVER"})

    by_player: dict[int, PlayerClaimResult] = {}
    for t in txns:
        team_name = getattr(t.team, "team_name", str(t.team)) if t.team else "Unknown"
        for item in t.items:
            if item.type != "ADD":
                continue
            result = by_player.setdefault(
                item.playerId, PlayerClaimResult(player_id=item.playerId, player_name=item.player, position=None)
            )
            # every distinct team that placed a real (nonzero) claim - a
            # team can appear multiple times across retries, only count once
            if t.bid_amount and not any(team == team_name for team, _ in result.all_bids):
                result.all_bids.append((team_name, t.bid_amount))
            if t.status == "EXECUTED":
                result.winner_team = team_name
                result.winning_bid = t.bid_amount

    return list(by_player.values())


def enrich_with_suggested_bids(
    claims: list[PlayerClaimResult],
    league,
    fp_api_key: str,
    season: int,
    position_slot_counts: dict,
    budget: float = 100.0,
    week: int | None = None,
) -> None:
    """Fills in .position and .suggested_bid on each claim, in place -
    live ESPN player_info() for position/percent_owned (one batched call,
    not one per player) + FantasyPros ROS rank (one call per position).

    `budget` should always be the caller's real live `league.settings.
    acquisition_budget`, not this default - this league's real budget
    has been $100 every season 2024-2026 (confirmed live 2026-09-16,
    see war_room_data.FAAB_BUDGET_TOTAL's own correction note - the
    caller here, scripts/post_waiver_recap.py, was passing the same
    wrong $200 this default used to be, which would have started
    suggesting DOUBLE the correct bid the moment metrics/waiver_value.
    py's POSITION_CEILINGS were doubled to fix that same bug elsewhere,
    if this call site hadn't been fixed alongside it).

    `week` should be the caller's real live `league.current_week` -
    passed through to position_scarcity_multipliers() so the QB
    superflex premium is damped early in the season (see that
    function's docstring, 2026-09-16)."""
    from . import fantasypros_client, player_matching

    player_ids = [c.player_id for c in claims]
    if not player_ids:
        return
    espn_players = league.player_info(playerId=player_ids)
    if not isinstance(espn_players, list):
        espn_players = [espn_players] if espn_players else []
    espn_by_id = {p.playerId: p for p in espn_players}

    for c in claims:
        p = espn_by_id.get(c.player_id)
        if p is not None:
            c.position = getattr(p, "position", None)

    scarcity = position_scarcity_multipliers(position_slot_counts, week=week)
    try:
        fp_by_position = fantasypros_client.fetch_all_ros_rankings(fp_api_key, season)
        espn_id_map = fantasypros_client.fetch_player_espn_id_map(fp_api_key)
    except Exception:  # noqa: BLE001 - suggested bids are a nice-to-have, never block the recap
        return

    for c in claims:
        if c.position is None:
            continue
        fp_position = "DST" if c.position == "D/ST" else c.position
        fp_players = fp_by_position.get(fp_position, [])
        espn_players_this_pos = [{"player_id": c.player_id, "player_name": c.player_name, "pro_team": None}]
        id_map = player_matching.match_players_for_position(
            espn_players_this_pos, fp_players, c.position, espn_id_map=espn_id_map
        )
        matched_fp_ids = [fp_id for fp_id, espn_id in id_map.items() if espn_id == c.player_id]
        if not matched_fp_ids:
            continue
        fp_player = next((p for p in fp_players if p["player_id"] == matched_fp_ids[0]), None)
        if not fp_player:
            continue
        pos_rank = player_matching.parse_pos_rank(fp_player.get("pos_rank"))
        percent_owned = getattr(espn_by_id.get(c.player_id), "percent_owned", None)
        c.suggested_bid = suggested_bid(pos_rank, percent_owned, c.position, scarcity, budget=budget)


def build_message(claims: list[PlayerClaimResult], week: int) -> str:
    executed = [c for c in claims if c.winner_team is not None]
    if not executed:
        return f"Week {week} waiver report: quiet week, nobody won a claim."

    executed.sort(key=lambda c: c.winning_bid or 0, reverse=True)

    lines = [f"💰 **WEEK {week} WAIVER WIRE REPORT**", ""]

    contested = [c for c in executed if c.contested]
    if contested:
        lines.append("⚔️ **CONTESTED CLAIMS**:")
        for c in contested:
            # all_bids includes the winner's own bid - don't restate it a
            # second time inside the parens (user, 2026-09-16: "when
            # recapping the contested bids, don't repeat the winning bid.
            # You already said it before the parentheses"), only the
            # OTHER bidders belong in the list here.
            other_bids = [(team, bid) for team, bid in c.all_bids if team != c.winner_team]
            others = ", ".join(f"{team} ${bid:.0f}" for team, bid in sorted(other_bids, key=lambda x: -x[1]))
            lines.append(
                f"- **{c.player_name}**: {c.winner_team} won at **${c.winning_bid:.0f}** (also bid: {others})"
            )
        lines.append("")

    overspent = [c for c in executed if c.overspent]
    if overspent:
        lines.append("📈 **PAID TOO MUCH** (per our own suggested value):")
        for c in overspent:
            lines.append(
                f"- {c.winner_team} spent **${c.winning_bid:.0f}** on **{c.player_name}** "
                f"(we'd have suggested ~${c.suggested_bid:.0f})"
            )
        lines.append("")

    steals = [c for c in executed if c.steal]
    if steals:
        lines.append("🎯 **STEALS** (we had them pegged for way more):")
        for c in steals:
            lines.append(
                f"- {c.winner_team} got **{c.player_name}** for just **${c.winning_bid:.0f}** "
                f"(we'd have suggested ~${c.suggested_bid:.0f})"
            )
        lines.append("")

    if len(lines) == 2:
        # Nothing contested/overpaid/underpaid enough to call out - still
        # say something rather than post a bare header (user, 2026-09-16
        # asked to drop the always-on "ALL EXECUTED CLAIMS" list, but a
        # genuinely quiet-but-not-empty week still deserves a real line).
        lines.append(f"{len(executed)} claim(s) processed - nothing notably contested, overpaid, or underpaid.")
    elif any(c.suggested_bid is not None for c in executed):
        if lines[-1] != "":
            lines.append("")
        lines.append("(suggested values are our own rough estimate, not gospel)")

    return "\n".join(lines).strip()
