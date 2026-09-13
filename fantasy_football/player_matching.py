"""Match FantasyPros players to our ESPN player_id. Pure/DB-free (no
network, no sqlite) so it's unit-testable like fantasy_football/metrics/ -
callers (ingest.py) do the DB reads/writes around this.

FantasyPros and ESPN don't share a player id, so matching is by name
(normalized) within position, except D/ST which matches far more
reliably by NFL team abbreviation than by name (ESPN "Texans D/ST" vs
FantasyPros "Houston Texans" share no common name tokens at all).
"""
from __future__ import annotations

import re

# FantasyPros' D/ST position code -> this project's ESPN-derived code.
POSITION_MAP = {"DST": "D/ST"}

# The only two team abbreviations that differ between FantasyPros'
# player_team_id and ESPN's pro_team (confirmed 2026-09-13 by diffing the
# full 32-team lists from both sources - every other abbreviation matches).
TEAM_ABBREV_FP_TO_ESPN = {"JAC": "JAX", "WAS": "WSH"}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NON_ALNUM_SPACE = re.compile(r"[^a-z0-9\s]")


def normalize_name(name: str) -> str:
    """Lowercase, drop apostrophes, turn hyphens/periods into spaces (so
    "Ja'Marr Chase" -> "jamarr chase" and "Amon-Ra St. Brown" -> "amon ra
    st brown" the same way on both sides), then drop a trailing Jr/Sr/
    numeral suffix so "Kenneth Walker III" matches "Kenneth Walker"."""
    name = (name or "").lower().strip().replace("'", "")
    name = name.replace("-", " ").replace(".", " ")
    name = _NON_ALNUM_SPACE.sub("", name)
    tokens = name.split()
    if len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def parse_pos_rank(pos_rank: str | int | None) -> int | None:
    """FantasyPros returns pos_rank as e.g. "QB1" - extract the trailing int."""
    if pos_rank is None:
        return None
    match = re.search(r"(\d+)$", str(pos_rank))
    return int(match.group(1)) if match else None


def _match_dst(espn_players: list[dict], fp_players: list[dict]) -> dict[int, int]:
    espn_by_team = {p["pro_team"]: p["player_id"] for p in espn_players if p.get("pro_team")}
    matches = {}
    for fp in fp_players:
        fp_team = fp.get("player_team_id")
        espn_team = TEAM_ABBREV_FP_TO_ESPN.get(fp_team, fp_team)
        espn_id = espn_by_team.get(espn_team)
        if espn_id is not None:
            matches[fp["player_id"]] = espn_id
    return matches


def _match_by_name(espn_players: list[dict], fp_players: list[dict]) -> dict[int, int]:
    by_name: dict[str, list[int]] = {}
    for p in espn_players:
        by_name.setdefault(normalize_name(p["player_name"]), []).append(p["player_id"])

    matches = {}
    for fp in fp_players:
        candidates = by_name.get(normalize_name(fp["player_name"]), [])
        if len(candidates) == 1:
            matches[fp["player_id"]] = candidates[0]
        # 0 candidates: not rostered anywhere right now, nothing to match.
        # >1 candidates: ambiguous (two same-named players at the same
        # position, both currently rostered) - skip rather than guess wrong.
    return matches


def match_players_for_position(espn_players: list[dict], fp_players: list[dict], espn_position: str) -> dict[int, int]:
    """espn_players/fp_players: dicts with at least player_id, player_name
    (and pro_team for D/ST, player_team_id for FantasyPros D/ST). Returns
    fp_player_id -> our espn player_id, for whichever fp_players could be
    confidently matched to a currently-rostered ESPN player."""
    if espn_position == "D/ST":
        return _match_dst(espn_players, fp_players)
    return _match_by_name(espn_players, fp_players)
