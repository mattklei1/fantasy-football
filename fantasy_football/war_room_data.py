"""WAR ROOM data layer: commissioner-only tools built on data this app
already has licensed access to (the paid FantasyPros API) or already
ingests (ESPN). Deliberately its own module, not dashboard_data.py -
nothing here is meant to reach a public-facing page. Callers must gate
access with ui_common.is_admin() themselves (see pages/9_War_Room.py);
this module does not check admin status on its own.

Unlike Roster Strength/the public waiver report, which only ever look at
players this league has actually rostered (see ingest.py's docstrings),
the Rankings Browser and Waiver Board here call FantasyPros/ESPN live for
the FULL player pool - that's the whole point of a "full" browser and a
real waiver board (most free agents were never rostered here, so they
have no row in fantasypros_rankings/player_rankings at all).
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from . import config, player_matching
from . import dashboard_data as dd
from .metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from .metrics.roster_strength import rank_to_score
from .metrics.waiver_value import position_scarcity_multipliers, suggested_bid

ESPN_POSITIONS = ["QB", "RB", "WR", "TE", "K", "D/ST"]


@st.cache_data(ttl=3600)
def get_fp_rankings_by_position(season: int) -> dict:
    """Live FantasyPros ROS rankings for all 6 positions, raw player dicts
    keyed by FantasyPros' own position code (QB/RB/WR/TE/K/DST). Cached an
    hour - a real hit against a paid API, and rest-of-season consensus
    rankings don't meaningfully move minute to minute. Empty dict (not an
    error) when no FANTASYPROS_API_KEY is configured, same "degrade, don't
    crash" pattern as Roster Strength."""
    from . import fantasypros_client

    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    try:
        return fantasypros_client.fetch_all_ros_rankings(api_key, season)
    except Exception:  # noqa: BLE001 - a War Room tool degrading is never worth crashing the page
        return {}


@st.cache_data(ttl=3600)
def get_fp_espn_id_map() -> dict[int, int]:
    from . import fantasypros_client

    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    try:
        return fantasypros_client.fetch_player_espn_id_map(api_key)
    except Exception:  # noqa: BLE001
        return {}


def _slot_counts(season: int) -> dict:
    meta = dd.get_season_meta(season)
    raw = meta.get("position_slot_counts")
    return json.loads(raw) if raw else {}


def get_position_slot_counts(season: int) -> dict:
    """Public wrapper for this season's real ESPN roster slot counts -
    callers (pages/9_War_Room.py) need this to call optimal_roster_value()/
    evaluate_trade() themselves without reaching into the private
    _slot_counts() helper."""
    return _slot_counts(season)


@st.cache_data(ttl=300)
def get_rankings_browser(season: int) -> pd.DataFrame:
    """Every FantasyPros-ranked player at every position - not just this
    league's rostered ones (that's what makes this "full", unlike Roster
    Strength's rostered-only signal) - cross-referenced against this
    league's current rosters so the commissioner can see who already owns
    a given name."""
    fp_by_position = get_fp_rankings_by_position(season)
    if not fp_by_position:
        return pd.DataFrame()

    conn = dd.get_connection()
    roster_rows = conn.execute(
        """
        SELECT wr.player_id, t.team_name
        FROM weekly_rosters wr JOIN teams t ON t.id = wr.team_pk
        WHERE wr.season_id = ? AND wr.week = (
            SELECT MAX(week) FROM weekly_rosters WHERE season_id = ?
        )
        """,
        (season, season),
    ).fetchall()
    team_by_espn_id = {r["player_id"]: r["team_name"] for r in roster_rows}
    espn_id_map = get_fp_espn_id_map()

    rows = []
    for fp_position, players in fp_by_position.items():
        espn_position = player_matching.POSITION_MAP.get(fp_position, fp_position)
        for p in players:
            espn_id = espn_id_map.get(p.get("player_id"))
            rows.append(
                {
                    "position": espn_position,
                    "player_name": p.get("player_name"),
                    "pro_team": p.get("player_team_id"),
                    "rank_ecr": p.get("rank_ecr"),
                    "pos_rank": player_matching.parse_pos_rank(p.get("pos_rank")),
                    "ros_points": p.get("r2p_pts"),
                    "rostered_by": team_by_espn_id.get(espn_id, "Free Agent"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("rank_ecr", na_position="last").reset_index(drop=True)


@st.cache_data(ttl=300)
def get_waiver_board(season: int, pool_size_per_position: int = 25) -> pd.DataFrame:
    """Every CURRENTLY AVAILABLE free agent, ranked by our own suggested-
    FAAB-value heuristic (metrics/waiver_value.py) - the same model the
    public GroupMe waiver report applies only after the fact to claims
    that already happened, run instead over the live free-agent pool so
    the commissioner can see who's worth a bid before Tuesday's claims lock."""
    from .espn_client import ESPNClient

    scarcity = position_scarcity_multipliers(_slot_counts(season))
    league = ESPNClient().get_league(season)
    fp_by_position = get_fp_rankings_by_position(season)
    espn_id_map = get_fp_espn_id_map()

    rows = []
    for position in ESPN_POSITIONS:
        try:
            free_agents = league.free_agents(size=pool_size_per_position, position=position)
        except Exception:  # noqa: BLE001 - one bad position shouldn't blank the whole board
            continue
        fp_position = "DST" if position == "D/ST" else position
        fp_players = fp_by_position.get(fp_position, [])
        espn_players = [
            {"player_id": p.playerId, "player_name": p.name, "pro_team": p.proTeam} for p in free_agents
        ]
        id_map = player_matching.match_players_for_position(
            espn_players, fp_players, position, espn_id_map=espn_id_map
        )
        fp_pos_rank_by_espn_id = {}
        for fp in fp_players:
            espn_id = id_map.get(fp["player_id"])
            if espn_id is not None:
                fp_pos_rank_by_espn_id[espn_id] = player_matching.parse_pos_rank(fp.get("pos_rank"))

        for p in free_agents:
            pos_rank = fp_pos_rank_by_espn_id.get(p.playerId)
            # ESPN returns -1 for "unknown", same convention ingest.py's
            # ingest_player_rankings() already normalizes to None.
            percent_owned = p.percent_owned if p.percent_owned != -1 else None
            percent_started = p.percent_started if p.percent_started != -1 else None
            # D/ST has no real injury status - espn_api returns [] (a
            # list) instead of a string there, which breaks Arrow
            # serialization if put straight into a DataFrame column
            # alongside real players' string statuses.
            injury_status = getattr(p, "injuryStatus", None)
            if not isinstance(injury_status, str):
                injury_status = None
            rows.append(
                {
                    "position": position,
                    "player_name": p.name,
                    "pro_team": p.proTeam,
                    "injury_status": injury_status,
                    "percent_owned": percent_owned,
                    "percent_started": percent_started,
                    "fp_pos_rank": pos_rank,
                    "suggested_bid": suggested_bid(pos_rank, percent_owned, position, scarcity),
                }
            )

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("suggested_bid", ascending=False, na_position="last").reset_index(drop=True)


@st.cache_data(ttl=300)
def get_trade_rosters(season: int) -> pd.DataFrame:
    """Every rostered player as of the latest ingested week, with a
    value_score blending FantasyPros ROS rank + ESPN season rank (the same
    rank_to_score() decay Roster Strength uses) and the same superflex QB
    premium the waiver model applies - so all 3 War Room tools agree on
    how this league's OP slot inflates QB value. Deliberately drops Roster
    Strength's weekly-projection signal: trade value should reflect
    rest-of-season outlook, not just this week's matchup."""
    conn = dd.get_connection()
    latest_row = conn.execute(
        "SELECT MAX(week) AS w FROM weekly_rosters WHERE season_id = ?", (season,)
    ).fetchone()
    latest_week = latest_row["w"] if latest_row else None
    if not latest_week:
        return pd.DataFrame()

    query = f"""
        SELECT wr.team_pk, t.team_name, {dd._manager_name_sql()} AS manager_name,
               wr.player_id, p.player_name, p.default_position AS position,
               wr.slot_position, wr.is_starter, wr.eligible_slots,
               fr.pos_rank AS fp_pos_rank, pr.pos_rank AS espn_pos_rank
        FROM weekly_rosters wr
        JOIN teams t ON t.id = wr.team_pk
        {dd._team_manager_join_sql()}
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN fantasypros_rankings fr
            ON fr.season_id = wr.season_id AND fr.week = wr.week AND fr.player_id = wr.player_id
        LEFT JOIN player_rankings pr
            ON pr.season_id = wr.season_id AND pr.week = wr.week AND pr.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ? AND wr.slot_position != 'IR'
    """
    df = pd.read_sql_query(query, conn, params=(season, latest_week))
    if df.empty:
        return df

    scarcity = position_scarcity_multipliers(_slot_counts(season))
    df["fp_score"] = df["fp_pos_rank"].apply(rank_to_score)
    df["espn_score"] = df["espn_pos_rank"].apply(rank_to_score)
    df["value_score"] = (
        df.apply(
            lambda row: blend_trade_value(row["fp_score"], row["espn_score"], row["position"], scarcity) * 100,
            axis=1,
        )
    ).round(1)
    return df.sort_values(["team_name", "value_score"], ascending=[True, False]).reset_index(drop=True)


# FantasyPros ROS rank weighted more heavily than ESPN's season-to-date
# rank for TRADE value specifically - same relative weighting as Roster
# Strength's fantasypros_ros(40)/season_rank(15) signals, just the two of
# them renormalized on their own since trade value drops the weekly-
# projection signal entirely (see get_trade_rosters' docstring).
TRADE_VALUE_WEIGHTS = {"fantasypros_ros": 40, "espn_season_rank": 15}


def blend_trade_value(fp_score: float | None, espn_score: float | None, position: str, scarcity_multipliers: dict) -> float:
    """Pure blend of the two rank-based signals into a 0-1ish trade value
    (uncapped above 1 for a scarcity-boosted position like QB in a
    superflex league - this is a relative comparison tool, not a
    percentage). Renormalizes over whichever signal(s) are actually
    present, same pattern as roster_strength.compute_player_values'
    blend() - a player missing one signal isn't penalized as if it were
    zero."""
    signals = [
        (TRADE_VALUE_WEIGHTS["fantasypros_ros"], fp_score),
        (TRADE_VALUE_WEIGHTS["espn_season_rank"], espn_score),
    ]
    present = [(w, v) for w, v in signals if pd.notna(v)]
    if not present:
        return 0.0
    total_weight = sum(w for w, _ in present)
    raw = sum(w * v for w, v in present) / total_weight
    return raw * scarcity_multipliers.get(position, 1.0)


# --- Marginal, starter-slot-aware trade valuation -------------------------
#
# Researched before building this (2026-09-13): real trade calculators and
# strategy writeups (FantasyCalc/DraftSharks-style tools, VORP-based
# analyses, dynasty consolidation-strategy pieces) converge on the same
# critique a naive "sum the players' values" trade calculator misses -
# summing raw values is wrong whenever the two sides trade different
# PLAYER COUNTS, because a roster can only start a fixed number of players
# per position. A 2-for-1 where the two outgoing players were both bench
# depth barely costs anything (you were never starting them anyway), while
# the incoming single star's FULL value counts (he takes a real starting
# spot). "Value over replacement" / stars-and-scrubs analyses land on the
# same idea from the opposite direction: a player's real worth is what he
# adds to your STARTING LINEUP, not his standalone rank.
#
# Rather than bolt on an arbitrary "10% per extra player" discount (a rule
# of thumb some calculators use), this computes it exactly: the MARGINAL
# change in each team's best-possible starting lineup value, before vs.
# after the trade, reusing the exact same Hungarian-algorithm optimal-
# lineup solver (metrics/lineup_optimizer.py) already built and tested for
# the Lineup Efficiency page's actual-vs-optimal points - just fed each
# player's ROS value_score instead of one week's real points. This single
# model structurally captures everything the research called out:
#   - 2-for-1s are valued correctly (bench filler contributes ~0 to the
#     "before" total, so giving it up costs ~0)
#   - team NEED is automatically priced in (an add at a position you're
#     already 3-deep at barely moves the total; the same player added at a
#     position where you're starting a replacement-level guy moves it a
#     lot)
#   - depth you can refill via waivers isn't overvalued (see
#     get_free_agent_value_ceiling() below, which flags exactly that)


def _roster_players(df: pd.DataFrame) -> list[RosterPlayer]:
    players = []
    for _, r in df.iterrows():
        try:
            slots = frozenset(json.loads(r["eligible_slots"]) or [])
        except (TypeError, ValueError):
            slots = frozenset()
        players.append(RosterPlayer(player_id=int(r["player_id"]), points=float(r["value_score"]), eligible_slots=slots))
    return players


def optimal_roster_value(df: pd.DataFrame, position_slot_counts: dict) -> tuple[float, set[int]]:
    """Best-possible STARTING LINEUP value_score total for a set of
    rostered players (columns: player_id, value_score, eligible_slots),
    plus the set of player_ids that lineup actually starts. Empty roster
    -> (0.0, empty set), not an error."""
    if df.empty:
        return 0.0, set()
    total, assignment = optimal_lineup(_roster_players(df), position_slot_counts)
    return total, set(assignment.values())


def evaluate_trade(
    rosters_df: pd.DataFrame,
    team_a: str,
    team_b: str,
    player_ids_out_a: list[int],
    player_ids_out_b: list[int],
    position_slot_counts: dict,
) -> dict:
    """The actual trade verdict: MARGINAL starting-lineup value gained or
    lost by each side (see module-level note above), not a flat sum of
    outgoing/incoming players' standalone values. Also reports which of a
    team's OWN remaining players newly enter or exit its optimal starting
    lineup as a side effect of the trade (e.g. your current RB2 gets
    bumped to bench because the player you acquired is better) - real
    roster-construction fallout a flat value sum can't show at all.

    rosters_df: full multi-team roster frame from get_trade_rosters()
    (needs team_name, player_id, player_name, value_score, eligible_slots).
    player_ids_out_a/b: player_ids team_a/team_b are sending away."""
    roster_a = rosters_df[rosters_df["team_name"] == team_a]
    roster_b = rosters_df[rosters_df["team_name"] == team_b]

    before_value_a, before_starters_a = optimal_roster_value(roster_a, position_slot_counts)
    before_value_b, before_starters_b = optimal_roster_value(roster_b, position_slot_counts)

    incoming_to_a = roster_b[roster_b["player_id"].isin(player_ids_out_b)]
    incoming_to_b = roster_a[roster_a["player_id"].isin(player_ids_out_a)]

    after_roster_a = pd.concat(
        [roster_a[~roster_a["player_id"].isin(player_ids_out_a)], incoming_to_a], ignore_index=True
    )
    after_roster_b = pd.concat(
        [roster_b[~roster_b["player_id"].isin(player_ids_out_b)], incoming_to_b], ignore_index=True
    )

    after_value_a, after_starters_a = optimal_roster_value(after_roster_a, position_slot_counts)
    after_value_b, after_starters_b = optimal_roster_value(after_roster_b, position_slot_counts)

    def _lineup_moves(before_starters, after_df, after_starters, name_by_id):
        newly_starting = [name_by_id[pid] for pid in (after_starters - before_starters) if pid in name_by_id]
        still_here = set(after_df["player_id"])
        newly_benched = [
            name_by_id[pid] for pid in (before_starters - after_starters) if pid in still_here and pid in name_by_id
        ]
        return newly_starting, newly_benched

    starting_a, benched_a = _lineup_moves(
        before_starters_a, after_roster_a, after_starters_a, dict(zip(after_roster_a["player_id"], after_roster_a["player_name"]))
    )
    starting_b, benched_b = _lineup_moves(
        before_starters_b, after_roster_b, after_starters_b, dict(zip(after_roster_b["player_id"], after_roster_b["player_name"]))
    )

    return {
        team_a: {
            "before": before_value_a, "after": after_value_a, "gain": after_value_a - before_value_a,
            "newly_starting": starting_a, "newly_benched": benched_a,
        },
        team_b: {
            "before": before_value_b, "after": after_value_b, "gain": after_value_b - before_value_b,
            "newly_starting": starting_b, "newly_benched": benched_b,
        },
    }


@st.cache_data(ttl=300)
def get_free_agent_value_ceiling(season: int) -> dict[str, float]:
    """Best currently-available free agent's value at each position,
    scaled onto the SAME axis as blend_trade_value()'s value_score
    (rank_to_score() * 100 * the same superflex scarcity multiplier) - so
    an outgoing bench player in a trade can be flagged "safely replaceable
    via waivers" rather than counted as a real loss, directly reflecting
    the read that finding a comparable replacement off waivers is a real,
    repeatable skill rather than something to price as if it were gone
    for good. Built from the live Waiver Board (get_waiver_board()) - no
    extra ESPN/FantasyPros calls."""
    board = get_waiver_board(season)
    if board.empty:
        return {}
    scarcity = position_scarcity_multipliers(_slot_counts(season))
    equivalent = board.apply(
        lambda r: (rank_to_score(r["fp_pos_rank"]) or 0.0) * 100 * scarcity.get(r["position"], 1.0), axis=1
    )
    return board.assign(value_equivalent=equivalent).groupby("position")["value_equivalent"].max().to_dict()
