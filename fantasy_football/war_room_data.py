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
        SELECT wr.team_pk, t.team_name, mgr.display_name AS manager_name,
               wr.player_id, p.player_name, p.default_position AS position,
               wr.slot_position, wr.is_starter,
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
