"""Suggested FAAB bid value for a free agent. Nobody actually publishes
this: checked FantasyPros' full API endpoint list (no waiver/FAAB
endpoint exists - their "FAAB Bid Tool" is website-only, off-limits per
their ToS same as everywhere else in this project), ESPN's Player object
(no bid/value field), and Yahoo (no public API at all, established
earlier). So this is OUR OWN heuristic, built from data already
available (FantasyPros ROS positional rank + ESPN's percent_owned as a
platform-wide demand signal) - not a borrowed number, and explicitly
normalized to THIS league's real $200 budget and real superflex slot
counts rather than assuming a generic $100/non-superflex baseline the
way any external tool would.

Deliberately transparent about being a heuristic, not a fact - every
message this feeds into should read as "here's roughly what this was
worth," not "the correct price."
"""
from __future__ import annotations

from .roster_strength import rank_to_score

MAX_BID_PCT_OF_BUDGET = 0.40  # a true league-altering pickup tops out around 40% of budget
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
    budget: float = 200.0,
) -> float | None:
    """Suggested FAAB bid in dollars for one free agent, or None if there's
    not enough signal to say anything (no FantasyPros rank at all)."""
    rank_score = rank_to_score(pos_rank)
    if rank_score is None:
        return None
    demand = (percent_owned or 0.0) / 100.0
    raw = RANK_WEIGHT * rank_score + DEMAND_WEIGHT * demand
    raw *= scarcity_multipliers.get(position, 1.0)
    return min(raw, 1.0) * MAX_BID_PCT_OF_BUDGET * budget
