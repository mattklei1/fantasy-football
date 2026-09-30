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
    # (team_name, bid, transaction_status) for every distinct team that
    # tried - status is ESPN's own raw code for THAT team's claim
    # (EXECUTED for the winner; FAILED_INVALIDPLAYERSOURCE for a normal
    # "someone else's claim beat mine to this player" loss; something
    # ELSE - e.g. FAILED_PLAYERALREADYDROPPED - means the claim was
    # invalid for a reason unrelated to being outbid, most often because
    # the SAME team had another claim earlier in the same run that used
    # up the roster spot this one needed (see build_message's footnote
    # logic - a real case found 2026-09-30: a $8 losing bid next to a
    # $5 winner looked like a bug until the raw transaction showed the
    # $8 claim's own intended drop had already been consumed by that
    # team's OWN higher-priority claim on a different player).
    all_bids: list[tuple[str, float, str | None]] = field(default_factory=list)
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


#: Human-readable reasons for the ESPN claim-failure statuses this
#: league has actually produced (see build_message's higher-losing-bid
#: footnote) - deliberately NOT trying to cover every status ESPN could
#: ever emit; an unrecognized one falls back to printing the raw status
#: code rather than guessing at a label.
_FAILURE_LABELS = {
    "FAILED_PLAYERALREADYDROPPED": "the player they meant to drop for it was already gone by the time it processed",
    "FAILED_ROSTERFULL": "no open roster spot",
    "FAILED_INSUFFICIENTBUDGET": "not enough FAAB budget left after their other claims",
}


def _status_rank(status: str | None) -> int:
    """How much a transaction status tells us about WHY a claim didn't
    execute - used to pick the most informative status when the same
    team's claim for the same player shows up more than once across
    ESPN's own processing retries (see fetch_week_claims). EXECUTED
    always wins outright (handled separately); among the rest, an
    explicit FAILED_* reason beats a leftover PENDING/CANCELED husk
    ESPN never bothered to update."""
    if not status:
        return 0
    if status == "EXECUTED":
        return 3
    if status.startswith("FAILED_"):
        return 2
    if status == "CANCELED":
        return 1
    return 0  # PENDING or anything unrecognized


def fetch_week_claims(league, scoring_period: int) -> list[PlayerClaimResult]:
    """One result per distinct player with at least one WAIVER-type claim
    this scoring period, win or lose."""
    txns = league.transactions(scoring_period=scoring_period, types={"WAIVER"})

    by_player: dict[int, PlayerClaimResult] = {}
    bid_by_player_team: dict[tuple[int, str], tuple[float, str | None]] = {}
    for t in txns:
        team_name = getattr(t.team, "team_name", str(t.team)) if t.team else "Unknown"
        for item in t.items:
            if item.type != "ADD":
                continue
            by_player.setdefault(
                item.playerId, PlayerClaimResult(player_id=item.playerId, player_name=item.player, position=None)
            )
            # every distinct team that placed a real (nonzero) claim - a
            # team can appear multiple times across retries, and not
            # always for the SAME amount: a team can submit several real
            # backup claims for the same player at different price
            # points with different drop targets (seen live 2026-09-30:
            # McConkey Kong tried Jaylen Wright at $1, $2, AND $4 across
            # 3 separate claims). Prefer the most informative status
            # (see _status_rank) first, and among a tie, the team's
            # highest real bid - the most aggressive amount they were
            # actually willing to pay, more representative than an
            # arbitrary "whichever attempt ESPN listed first."
            if t.bid_amount:
                key = (item.playerId, team_name)
                existing = bid_by_player_team.get(key)
                if existing is None:
                    bid_by_player_team[key] = (t.bid_amount, t.status)
                else:
                    existing_bid, existing_status = existing
                    new_rank, existing_rank = _status_rank(t.status), _status_rank(existing_status)
                    if new_rank > existing_rank or (new_rank == existing_rank and t.bid_amount > existing_bid):
                        bid_by_player_team[key] = (t.bid_amount, t.status)
            if t.status == "EXECUTED":
                result = by_player[item.playerId]
                result.winner_team = team_name
                result.winning_bid = t.bid_amount

    for (player_id, team_name), (bid, status) in bid_by_player_team.items():
        by_player[player_id].all_bids.append((team_name, bid, status))

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
            other_bids = [(team, bid, status) for team, bid, status in c.all_bids if team != c.winner_team]
            others = ", ".join(f"{team} ${bid:.0f}" for team, bid, _status in sorted(other_bids, key=lambda x: -x[1]))
            lines.append(
                f"- **{c.player_name}**: {c.winner_team} won at **${c.winning_bid:.0f}** (also bid: {others})"
            )
            # A losing bid can genuinely be HIGHER than the winning one -
            # ESPN's own FAILED_INVALIDPLAYERSOURCE just means "someone
            # else's claim got there first," which is a normal loss
            # regardless of amount, but anything else (most often
            # FAILED_PLAYERALREADYDROPPED - that bidder's OWN earlier,
            # higher-priority claim already used up the roster spot this
            # one needed) means the claim was invalid for a reason that
            # has nothing to do with being outbid. Flag that explicitly -
            # user, 2026-09-30, catching exactly this on Kendre Miller
            # ($5 winner next to an unexplained $8 loser): "Are your
            # contested bids right? Kendre miller has a losing bid at a
            # higher $ amount" - verified against the real ESPN
            # transaction log; without this note a higher losing bid
            # reads as a bug in the recap itself.
            winning_bid = c.winning_bid or 0
            for team, bid, status in other_bids:
                if bid > winning_bid and status not in (None, "FAILED_INVALIDPLAYERSOURCE"):
                    reason = _FAILURE_LABELS.get(status, f"raw ESPN status: {status}")
                    lines.append(
                        f"  ↳ {team}'s ${bid:.0f} bid was actually higher, but their claim never "
                        f"executed - {reason}"
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
