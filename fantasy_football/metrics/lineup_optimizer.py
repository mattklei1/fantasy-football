"""Optimal legal starting lineup solver. Given a roster's per-player
points and slot eligibility for a week, plus the league's real roster
slot requirements, find the assignment of players to STARTING slots
that maximizes total points - exactly matching the spec requirement to
"respect real roster/slot settings pulled from ESPN... never hardcode
positions."

This is a maximum-weight bipartite matching problem (players x slot
instances), solved exactly via scipy's Hungarian algorithm
implementation rather than a greedy heuristic - a greedy "fill the most
valuable flex first" approach can produce a genuinely suboptimal lineup
when a player is needed to fill a scarce dedicated slot but greedy
already used them in a flex spot. The problem is tiny (at most ~17
players x ~10 slots), so exact solving costs nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

BENCH_LIKE_SLOTS = {"BE", "IR", ""}
IMPOSSIBLE_COST = 1e6  # effectively forbids an ineligible player/slot pairing


@dataclass(frozen=True)
class RosterPlayer:
    player_id: int
    points: float
    eligible_slots: frozenset[str]


def starting_slots(position_slot_counts: dict[str, int]) -> list[str]:
    """Expand {'QB': 1, 'RB': 2, ...} into individual slot instances,
    excluding BE/IR (those aren't starting slots to optimize)."""
    slots = []
    for slot_name, count in position_slot_counts.items():
        if slot_name in BENCH_LIKE_SLOTS or not count or count <= 0:
            continue
        slots.extend([slot_name] * int(count))
    return slots


def optimal_lineup(players: list[RosterPlayer], position_slot_counts: dict[str, int]) -> tuple[float, dict[str, int]]:
    """Returns (optimal_points, {slot_instance_label: player_id}).
    slot_instance_label is e.g. "RB#1", "RB#2" for duplicate slot types."""
    slots = starting_slots(position_slot_counts)
    if not slots or not players:
        return 0.0, {}

    n_players, n_slots = len(players), len(slots)
    # cost matrix: minimize cost == maximize points, so cost = -points
    # where eligible, else an effectively-forbidden large cost
    cost = np.full((n_players, n_slots), IMPOSSIBLE_COST)
    for i, p in enumerate(players):
        for j, slot in enumerate(slots):
            if slot in p.eligible_slots:
                cost[i, j] = -p.points

    row_ind, col_ind = linear_sum_assignment(cost)

    total_points = 0.0
    assignment: dict[str, int] = {}
    slot_instance_counts: dict[str, int] = {}
    for r, c in zip(row_ind, col_ind):
        if cost[r, c] >= IMPOSSIBLE_COST:
            continue  # this slot genuinely couldn't be filled legally - leave empty
        slot_name = slots[c]
        slot_instance_counts[slot_name] = slot_instance_counts.get(slot_name, 0) + 1
        label = f"{slot_name}#{slot_instance_counts[slot_name]}"
        assignment[label] = players[r].player_id
        total_points += players[r].points

    return total_points, assignment
