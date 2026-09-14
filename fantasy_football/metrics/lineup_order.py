"""Decides WHICH SPECIFIC SLOT LABEL each already-chosen skill-position
starter gets (base RB/WR/TE vs the RB/WR/TE flex slot) - NOT who starts,
that's decided upstream by rank value. Preference: the flex slot(s)
should hold whichever starter(s) have the LATEST kickoff time, and the
fixed base slots should hold the earliest-kickoff starters - a real
manager's tactic (lock in decisions you can't change anyway, keep your
flex flexibility for the game that decides latest), not a scoring
difference (ESPN scores a player identically regardless of which
eligible slot label they're sitting in).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .lineup_optimizer import IMPOSSIBLE_COST

FLEX_SLOT_NAME = "RB/WR/TE"


@dataclass(frozen=True)
class TimedPlayer:
    player_id: int
    eligible_slots: frozenset[str]
    kickoff: datetime.datetime


def order_flex_pool_by_kickoff(players: list[TimedPlayer], slot_counts: dict[str, int]) -> dict[int, str]:
    """players: the FIXED set of already-chosen skill-position starters
    (exactly as many as sum(slot_counts.values())). slot_counts: e.g.
    {"RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 2}. Returns {player_id:
    slot_name}. Uses the same Hungarian-assignment approach as
    lineup_optimizer.optimal_lineup, but the "value" being maximized
    here is "put the latest kickoff time in a flex slot" rather than
    points - a legal (eligibility-respecting) assignment is still
    guaranteed since cost outside a player's own eligible_slots is
    forbidden, same as the points-based solver."""
    slots: list[str] = []
    for slot_name, count in slot_counts.items():
        slots.extend([slot_name] * int(count))
    if not slots or not players:
        return {}

    n_players, n_slots = len(players), len(slots)
    cost = np.full((n_players, n_slots), IMPOSSIBLE_COST)
    for i, p in enumerate(players):
        for j, slot in enumerate(slots):
            if slot not in p.eligible_slots:
                continue
            # Flex slots reward a LATER kickoff (minimize cost ==
            # maximize -timestamp == prefer latest); base slots are
            # neutral so the solver's only real preference is which 2
            # (or however many) players get the flex label.
            cost[i, j] = -p.kickoff.timestamp() if slot == FLEX_SLOT_NAME else 0.0

    row_ind, col_ind = linear_sum_assignment(cost)
    assignment: dict[int, str] = {}
    for r, c in zip(row_ind, col_ind):
        if cost[r, c] >= IMPOSSIBLE_COST:
            continue
        assignment[players[r].player_id] = slots[c]
    return assignment
