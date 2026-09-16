"""Suggested FAAB bid value for a free agent. Nobody actually publishes
this: checked FantasyPros' full API endpoint list (no waiver/FAAB
endpoint exists - their "FAAB Bid Tool" is website-only, off-limits per
their ToS same as everywhere else in this project), ESPN's Player object
(no bid/value field), and Yahoo (no public API at all, established
earlier). So this is OUR OWN heuristic, built from data already
available (FantasyPros ROS positional rank + ESPN's percent_owned as a
platform-wide demand signal) - not a borrowed number, and explicitly
normalized to THIS league's real superflex slot counts rather than
assuming a generic non-superflex baseline the way any external tool
would. Only applies at all to a league that actually uses FAAB
(`league.settings.faab`) - some registered leagues use plain waiver-
PRIORITY claims instead, where a dollar figure is meaningless (see
war_room_data.get_waiver_board's docstring).

Deliberately transparent about being a heuristic, not a fact - every
message this feeds into should read as "here's roughly what this was
worth," not "the correct price."

CALIBRATED against this league's own real 2025 waiver history (2026-09-
13), not just assumed - pulled every executed, nonzero WAIVER bid for
the full season (108 real claims) and looked at the max bid actually
paid per position, as a % of budget:

    QB 40% ($80 max) | RB 25.5% ($51) | WR 12% ($24)
    TE 7.5% ($15)    | D/ST 3% ($6)   | K 0.5% ($1, n=1 - thin sample)

CORRECTION (2026-09-16): the percentages above, and POSITION_CEILINGS
below, were originally computed against an assumed $200 budget - this
league's REAL `league.settings.acquisition_budget` has always actually
been $100 (confirmed live, every season 2024-2026; found while
investigating a separate report that non-FAAB leagues were being shown
a fabricated $ bid at all). Every ceiling below is DOUBLED from its
originally-published value so the real calibrated DOLLAR amounts above
($80 QB max, $51 RB max, etc. - the actual real-world facts this was
calibrated against) stay exactly what they were; only the fraction
that expresses them as a fraction of the correct $100, not the wrong
$200, changes. Before this whole calibration, every position shared
ONE flat 40%-of-budget ceiling (POSITION_CEILINGS below) - fine for QB,
since that happens to match the real QB max almost exactly, but it
meant D/ST could suggest $30-40 when real managers never paid more than
$6 for one all season - an order of magnitude too high. POSITION_
CEILINGS fixes this with a per-position ceiling instead of one shared
number, each set at the real observed max with modest headroom (one
season's max is a real number, not a hard limit - a truly special
player could exceed it).

Re-ran the full 2025 season through the RECALIBRATED formula afterward
to check the fix actually worked, not just assumed it did (99/108 real
claims matched to a suggested value; RB/TE showed 0 matches on a first
pass - traced to FantasyPros' own API returning 0 results for
`consensus-rankings?season=2025` for those two positions specifically
while every other position/season combination works fine, a FantasyPros-
side quirk, not a matching bug - worked around by pointing the backtest
at the current season's rankings applied retroactively, same as
production always does anyway). Real mean vs. suggested mean, and real
max vs. suggested max, per position:

    QB:   real $17 avg / $80 max  ->  suggested $26 avg / $50 max
    RB:   real  $9 avg / $51 max  ->  suggested $13 avg / $24 max
    WR:   real  $6 avg / $24 max  ->  suggested  $6 avg / $11 max
    TE:   real  $7 avg / $15 max  ->  suggested  $8 avg / $14 max
    D/ST: real  $3 avg /  $6 max  ->  suggested  $4 avg /  $8 max
    K:    real  $1 (n=1)          ->  suggested  $4 (n=1)

D/ST went from suggesting 10-15x real value to within a couple dollars.
Every position's suggested max now sits in the same order of magnitude
as the real max. The remaining richness on some positions' MEAN (QB/RB/
D/ST run ~1.3-1.7x over their real average, while WR lands almost
exactly on it) is most likely the ceiling this comparison itself can't
fully escape - the suggested side necessarily uses CURRENT rankings
applied retroactively to last year's transactions (FantasyPros' ROS
data is inherently "now," never historical - see the Roster Strength
data-sourcing note elsewhere in this project for the same constraint),
so a player's real 2025 waiver-wire value and his 2026-rankings-implied
value aren't quite the same thing being compared. Deliberately NOT
chased further with more formula tuning based on one season's noisy,
partially-mismatched data - revisit once a live season's real
transactions can be compared against that same season's live rankings,
a genuinely apples-to-apples check this backtest can't fully deliver.
"""
from __future__ import annotations

from .roster_strength import rank_to_score

# Per-position ceiling (max suggested bid as a fraction of the total
# budget) - see the calibration note above for where these numbers come
# from, and the 2026-09-16 correction note for why these are double
# their originally-published values (real budget is $100, not the $200
# these were first computed against - doubled to keep the same real
# calibrated DOLLAR ceilings, not to raise them). K has only 1 real data
# point (n=1, $1) - treated as a weak signal, so its ceiling gets more
# headroom than the sample alone would suggest, not a literal 1%+epsilon.
POSITION_CEILINGS = {
    "QB": 0.80,
    "RB": 0.56,
    "WR": 0.30,
    "TE": 0.20,
    "D/ST": 0.10,
    "K": 0.06,
}
DEFAULT_CEILING = 0.30  # fallback for any position not in the table above
RANK_WEIGHT = 0.7
DEMAND_WEIGHT = 0.3


def position_scarcity_multipliers(position_slot_counts: dict) -> dict[str, float]:
    """Superflex-aware QB premium, derived from THIS season's real
    position_slot_counts rather than assumed. An OP ("offensive player")
    slot overwhelmingly gets filled by a 2nd startable QB in practice -
    that's the entire reason "superflex" leagues are colloquially called
    that, since real NFL-startable QB depth (~20-24 players) is much
    thinner than RB/WR/TE depth, which already has plenty of flex-
    eligible bodies regardless of slot count. Every other position gets
    a neutral 1.0x. Each additional OP slot is modeled as roughly
    doubling per-team startable-QB demand (a 0.5x step per slot) - a
    reasonable, commonly-cited superflex premium range, not a precise
    market simulation."""
    qb_slots = position_slot_counts.get("QB", 1) or 1
    op_slots = position_slot_counts.get("OP", 0) or 0
    return {"QB": 1.0 + 0.5 * (op_slots / qb_slots)}


def suggested_bid(
    pos_rank: int | None,
    percent_owned: float | None,
    position: str,
    scarcity_multipliers: dict[str, float],
    budget: float = 100.0,
) -> float | None:
    """Suggested FAAB bid in dollars for one free agent, or None if there's
    not enough signal to say anything (no FantasyPros rank at all). This
    default is only a fallback for a caller that can't look up the real
    per-league budget - always prefer passing the live
    `league.settings.acquisition_budget` instead (see war_room_data.
    get_waiver_board)."""
    rank_score = rank_to_score(pos_rank)
    if rank_score is None:
        return None
    demand = (percent_owned or 0.0) / 100.0
    raw = RANK_WEIGHT * rank_score + DEMAND_WEIGHT * demand
    raw *= scarcity_multipliers.get(position, 1.0)
    ceiling = POSITION_CEILINGS.get(position, DEFAULT_CEILING)
    return min(raw, 1.0) * ceiling * budget
