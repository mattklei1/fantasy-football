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
import requests
import streamlit as st

from . import config, player_matching
from . import dashboard_data as dd
from .metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from .metrics.roster_strength import rank_to_score
from .metrics.waiver_value import position_scarcity_multipliers, suggested_bid

ESPN_POSITIONS = ["QB", "RB", "WR", "TE", "K", "D/ST"]


@st.cache_data(ttl=3600)
def _fetch_fp_rankings_by_position_raw(season: int, api_key: str) -> dict:
    from . import fantasypros_client

    return fantasypros_client.fetch_all_ros_rankings(api_key, season)


def get_fp_rankings_by_position(season: int) -> dict:
    """Live FantasyPros ROS rankings for all 6 positions, raw player dicts
    keyed by FantasyPros' own position code (QB/RB/WR/TE/K/DST). The
    actual fetch is cached an hour (a real hit against a paid API, and
    rest-of-season consensus rankings don't meaningfully move minute to
    minute) - but ONLY a successful fetch (an exception raised inside a
    st.cache_data-decorated call is never cached, so it's retried fresh
    on the very next page load, not just here). This wrapper is
    deliberately uncached itself: catching the failure INSIDE the cached
    function would cache the EMPTY result for the full hour too, turning
    one transient FantasyPros hiccup into "blank for everyone" for an
    hour (a real bug this fixes, not a hypothetical - see PROJECT_BRIEF).
    Empty dict (not an error) when no FANTASYPROS_API_KEY is configured,
    same "degrade, don't crash" pattern as Roster Strength."""
    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    try:
        return _fetch_fp_rankings_by_position_raw(season, api_key)
    except Exception:  # noqa: BLE001 - a War Room tool degrading is never worth crashing the page
        return {}


@st.cache_data(ttl=3600)
def _fetch_fp_overall_rank_by_fp_id_raw(season: int, api_key: str) -> dict[int, int]:
    from . import fantasypros_client

    players = fantasypros_client.fetch_overall_ros_rankings(api_key, season)
    return {p["player_id"]: p.get("rank_ecr") for p in players if p.get("player_id") is not None}


def get_fp_overall_rank_by_fp_id(season: int) -> dict[int, int]:
    """FantasyPros' TRUE cross-position rest-of-season rank (position=
    "ALL"), keyed by FantasyPros' own player_id so it can be joined
    against get_fp_rankings_by_position()'s per-position player dicts
    (confirmed live that player_id is consistent between the two calls).
    NOT the same number as those per-position dicts' own `rank_ecr` field
    - that's just each position's rank restated (K1's rank_ecr is 1, not
    ~186) - see fantasypros_client.fetch_overall_ros_rankings()'s
    docstring for the live verification. Empty dict (not an error) when
    no FANTASYPROS_API_KEY is configured. See get_fp_rankings_by_
    position()'s docstring for why this wrapper is deliberately
    uncached - only the successful raw fetch is."""
    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    try:
        return _fetch_fp_overall_rank_by_fp_id_raw(season, api_key)
    except Exception:  # noqa: BLE001 - a War Room tool degrading is never worth crashing the page
        return {}


@st.cache_data(ttl=3600)
def _fetch_fp_espn_id_map_raw(api_key: str) -> dict[int, int]:
    from . import fantasypros_client

    return fantasypros_client.fetch_player_espn_id_map(api_key)


def get_fp_espn_id_map() -> dict[int, int]:
    """See get_fp_rankings_by_position()'s docstring for why this wrapper
    is deliberately uncached - only the successful raw fetch is."""
    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    try:
        return _fetch_fp_espn_id_map_raw(api_key)
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
def get_current_week(season: int) -> int:
    """The live current NFL week per ESPN - used to key the Lineup
    Optimizer's rank-override upload independently of whatever historical
    week the sidebar's own week picker happens to be set to (lineup
    decisions are always about the CURRENT week, never a past one)."""
    from .espn_client import ESPNClient

    return ESPNClient().get_league(season).current_week


@st.cache_data(ttl=300)
def get_my_team_pk(season: int) -> int | None:
    """Resolves 'my team' (the logged-in admin's own ESPN team) by
    matching this deployment's configured SWID against the live
    league's real team owners - robust to a team being renamed in ESPN
    (unlike matching on team name, which would silently break the day
    someone renames - confirmed this actually happened this season, see
    PROJECT_BRIEF). Same approach scripts/post_waiver_recommendations.py
    uses for the identical problem. Returns the DB team_pk that
    get_trade_rosters()/get_waiver_board() key off of, or None if no
    match (degrade gracefully rather than crash - callers should fall
    back to a manual picker)."""
    from .espn_client import ESPNClient

    try:
        client = ESPNClient()
        league = client.get_league(season)
        swid = client.credentials.swid
        my_team = next((t for t in league.teams if any(o.get("id") == swid for o in t.owners)), None)
        if my_team is None:
            return None
        conn = dd.get_connection()
        row = conn.execute(
            "SELECT id FROM teams WHERE season_id = ? AND espn_team_id = ?", (season, my_team.team_id)
        ).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001 - a nice-to-have default, never worth crashing the page
        return None


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
    overall_rank_by_fp_id = get_fp_overall_rank_by_fp_id(season)

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
                    "overall_rank": overall_rank_by_fp_id.get(p.get("player_id")),
                    "pos_rank": player_matching.parse_pos_rank(p.get("pos_rank")),
                    "ros_points": p.get("r2p_pts"),
                    "rostered_by": team_by_espn_id.get(espn_id, "Free Agent"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("overall_rank", na_position="last").reset_index(drop=True)


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
    overall_rank_by_fp_id = get_fp_overall_rank_by_fp_id(season)

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
        fp_ros_points_by_espn_id = {}
        fp_overall_rank_by_espn_id = {}
        for fp in fp_players:
            espn_id = id_map.get(fp["player_id"])
            if espn_id is not None:
                fp_pos_rank_by_espn_id[espn_id] = player_matching.parse_pos_rank(fp.get("pos_rank"))
                fp_ros_points_by_espn_id[espn_id] = fp.get("r2p_pts")
                fp_overall_rank_by_espn_id[espn_id] = overall_rank_by_fp_id.get(fp["player_id"])

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
                    "player_id": p.playerId,
                    "position": position,
                    "player_name": p.name,
                    "pro_team": p.proTeam,
                    "injury_status": injury_status,
                    "percent_owned": percent_owned,
                    "percent_started": percent_started,
                    "fp_pos_rank": pos_rank,
                    "overall_rank": fp_overall_rank_by_espn_id.get(p.playerId),
                    "ros_points": fp_ros_points_by_espn_id.get(p.playerId),
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


DEFAULT_WIN_WIN_GAIN_THRESHOLD = 5.0  # same "counts as a real gain, not a wash" bar pages/9_War_Room.py uses for its own verdict


def find_win_win_trades(
    rosters_df: pd.DataFrame, my_team: str, position_slot_counts: dict,
    gain_threshold: float = DEFAULT_WIN_WIN_GAIN_THRESHOLD, max_results: int = 10,
) -> list[dict]:
    """Searches every other team for realistic trades with `my_team`
    where BOTH sides' optimal starting lineup value goes up by more than
    gain_threshold via evaluate_trade() - a genuine win-win, not just a
    trade my_team wins. Deliberately bounded to realistic trade shapes
    rather than every possible subset of both rosters (a ~17-player
    roster's full subset space would combinatorially explode across 11
    opponents): 1-for-1 (every one of my_team's players against every
    one of the opponent's), plus 2-for-1 in each direction using each
    side's own 2 LOWEST-value players as the throw-in pair - real
    2-for-1 offers almost always pair a real piece with the weakest
    bench depth, not a random 2, and evaluate_trade's marginal-value
    model already prices a bench piece that won't crack the lineup at
    ~0 cost regardless of exactly which one it is, so this doesn't miss
    real candidates, just skips re-checking near-identical throw-ins.

    Returns up to max_results dicts, sorted by min(my_gain, their_gain)
    descending - the MOST mutually beneficial trades first, not just
    the ones that most favor my_team: {opponent, players_out,
    players_in, my_gain, their_gain}."""
    my_roster = rosters_df[rosters_df["team_name"] == my_team]
    if my_roster.empty:
        return []

    my_worst_two = my_roster.nsmallest(2, "value_score")["player_id"].tolist() if len(my_roster) >= 2 else []

    candidates = []
    other_teams = [t for t in rosters_df["team_name"].dropna().unique() if t != my_team]
    for opp in other_teams:
        opp_roster = rosters_df[rosters_df["team_name"] == opp]
        if opp_roster.empty:
            continue
        opp_worst_two = opp_roster.nsmallest(2, "value_score")["player_id"].tolist() if len(opp_roster) >= 2 else []

        trial_trades = [([mine], [theirs]) for mine in my_roster["player_id"] for theirs in opp_roster["player_id"]]
        if my_worst_two:
            trial_trades += [(my_worst_two, [theirs]) for theirs in opp_roster["player_id"]]
        if opp_worst_two:
            trial_trades += [([mine], opp_worst_two) for mine in my_roster["player_id"]]

        for out_ids, in_ids in trial_trades:
            result = evaluate_trade(rosters_df, my_team, opp, out_ids, in_ids, position_slot_counts)
            my_gain, their_gain = result[my_team]["gain"], result[opp]["gain"]
            if my_gain > gain_threshold and their_gain > gain_threshold:
                candidates.append(
                    {
                        "opponent": opp,
                        "players_out": my_roster[my_roster["player_id"].isin(out_ids)]["player_name"].tolist(),
                        "players_in": opp_roster[opp_roster["player_id"].isin(in_ids)]["player_name"].tolist(),
                        "my_gain": my_gain,
                        "their_gain": their_gain,
                    }
                )

    candidates.sort(key=lambda c: min(c["my_gain"], c["their_gain"]), reverse=True)
    return candidates[:max_results]


@st.cache_data(ttl=300)
def _free_agent_value_equivalent_column(board: pd.DataFrame, scarcity: dict) -> pd.Series:
    """Every free agent's OWN value, scaled onto the SAME axis as
    blend_trade_value()'s value_score (rank_to_score() * 100 * the same
    superflex scarcity multiplier) - shared by get_free_agent_value_ceiling()
    (aggregated to a per-position max) and get_my_waiver_suggestions()
    (used per-player, so a suggestion can be compared directly against a
    specific rostered player's value_score, not just the position ceiling)."""
    return board.apply(
        lambda r: (rank_to_score(r["fp_pos_rank"]) or 0.0) * 100 * scarcity.get(r["position"], 1.0), axis=1
    )


def get_free_agent_value_ceiling(season: int) -> dict[str, float]:
    """Best currently-available free agent's value at each position - so
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
    equivalent = _free_agent_value_equivalent_column(board, scarcity)
    return board.assign(value_equivalent=equivalent).groupby("position")["value_equivalent"].max().to_dict()


MAX_SUGGESTIONS_PER_POSITION = 3  # keep the top-10 diversified across positions, not e.g. all QBs in a superflex league
FAAB_BUDGET_TOTAL = 200.0  # this league's real budget - see metrics/waiver_value.py's calibration note


def build_waiver_suggestion_reasoning(
    position: str,
    fa_value: float,
    my_best_at_position: float | None,
    bench_depth_at_position: int,
    suggested_bid: float | None,
    budget_remaining: float,
) -> str:
    """Pure sentence-template reasoning (no LLM) for one suggested add -
    "Claude never computes stats" extends here too: every number quoted
    is a real fact already computed elsewhere (value_score, suggested_bid,
    ESPN's own tracked FAAB spend), this function only phrases it."""
    reasons = []
    if my_best_at_position is None:
        reasons.append(f"you have nobody rostered at {position} at all")
    elif fa_value > my_best_at_position:
        reasons.append(
            f"projects ahead of your current best {position} ({fa_value:.0f} vs {my_best_at_position:.0f} value) "
            "- a real starter upgrade, not just bench insurance"
        )
    if bench_depth_at_position == 0:
        reasons.append(f"zero bench depth at {position} right now")
    elif bench_depth_at_position == 1:
        reasons.append(f"only one bench player at {position}")
    if suggested_bid is not None and suggested_bid > budget_remaining:
        reasons.append(f"NOTE: ${suggested_bid:.0f} suggested bid exceeds your ${budget_remaining:.0f} left - bid lower")
    if not reasons:
        reasons.append(f"best available {position} on waivers right now")
    return "; ".join(reasons)


@st.cache_data(ttl=300)
def get_my_waiver_suggestions(season: int, my_team_pk: int, top_n: int = 10) -> list[dict]:
    """Top-N suggested waiver adds for ONE team (the admin's own), each
    with a specific suggested drop from that team's own roster and
    plain-language reasoning (position need, value gap, remaining FAAB
    budget) - not just a ranked free-agent list. Deterministic throughout:
    every number is a real computed fact (value_score, suggested_bid, and
    ESPN's own per-team `acquisitionBudgetSpent` tracker for the real
    remaining budget) - no LLM involved anywhere in this function.

    Reuses get_waiver_board() (live free agents) and get_trade_rosters()
    (this team's current roster + value_score) - no extra ESPN/FantasyPros
    calls beyond what those two already make."""
    board = get_waiver_board(season)
    rosters = get_trade_rosters(season)
    if board.empty or rosters.empty:
        return []

    my_roster = rosters[rosters["team_pk"] == my_team_pk]
    if my_roster.empty:
        return []

    from .espn_client import ESPNClient

    conn = dd.get_connection()
    espn_team_id_row = conn.execute("SELECT espn_team_id FROM teams WHERE id = ?", (my_team_pk,)).fetchone()
    budget_remaining = FAAB_BUDGET_TOTAL
    if espn_team_id_row:
        try:
            league = ESPNClient().get_league(season)
            espn_team = next((t for t in league.teams if t.team_id == espn_team_id_row["espn_team_id"]), None)
            if espn_team is not None:
                budget_remaining = FAAB_BUDGET_TOTAL - (espn_team.acquisition_budget_spent or 0)
        except Exception:  # noqa: BLE001 - a live ESPN hiccup shouldn't block suggestions, just skip the budget check
            pass

    scarcity = position_scarcity_multipliers(_slot_counts(season))
    board = board.assign(value_equivalent=_free_agent_value_equivalent_column(board, scarcity))

    best_by_position = my_roster.groupby("position")["value_score"].max().to_dict()
    bench = my_roster[my_roster["is_starter"] == 0].sort_values("value_score")
    bench_depth_by_position = bench.groupby("position").size().to_dict()

    # "do not drop" players (see get_protected_player_ids) are excluded
    # from the DROP CANDIDATE pool specifically - they still count as
    # real bench depth for the reasoning text above (a protected player
    # is a real roster fact either way), just never picked as the
    # suggested drop themselves (user feedback 2026-09-14).
    protected_ids = get_protected_player_ids(my_team_pk)
    droppable_bench = bench[~bench["player_id"].isin(protected_ids)] if protected_ids else bench

    suggestions: list[dict] = []
    per_position_count: dict[str, int] = {}
    ranked = board[board["suggested_bid"].notna()].sort_values("suggested_bid", ascending=False)
    for _, fa in ranked.iterrows():
        position = fa["position"]
        if per_position_count.get(position, 0) >= MAX_SUGGESTIONS_PER_POSITION:
            continue

        same_position_bench = droppable_bench[droppable_bench["position"] == position]
        if not same_position_bench.empty:
            drop_candidate = same_position_bench.iloc[0]
        elif not droppable_bench.empty:
            drop_candidate = droppable_bench.iloc[0]
        else:
            drop_candidate = None

        bench_depth = int(bench_depth_by_position.get(position, 0))
        my_best_here = best_by_position.get(position)
        reasoning = build_waiver_suggestion_reasoning(
            position, float(fa["value_equivalent"]), my_best_here, bench_depth, fa["suggested_bid"], budget_remaining
        )

        suggestions.append(
            {
                "player_id": int(fa["player_id"]),
                "player_name": fa["player_name"],
                "position": position,
                "pro_team": fa["pro_team"],
                "suggested_bid": float(fa["suggested_bid"]),
                "suggested_drop": drop_candidate["player_name"] if drop_candidate is not None else None,
                "suggested_drop_id": int(drop_candidate["player_id"]) if drop_candidate is not None else None,
                "suggested_drop_value": float(drop_candidate["value_score"]) if drop_candidate is not None else None,
                "reasoning": reasoning,
                "affordable": bool(fa["suggested_bid"] <= budget_remaining),
            }
        )
        per_position_count[position] = per_position_count.get(position, 0) + 1
        if len(suggestions) >= top_n:
            break

    return suggestions


@st.cache_data(ttl=300)
def get_droppable_roster(season: int, team_pk: int) -> pd.DataFrame:
    """This team's bench players only, sorted worst-value-first (most
    droppable first) - the exact same pool get_my_waiver_suggestions()
    already draws its suggested drops from, surfaced here so the admin
    can browse and pick a manual drop for a claim outside the auto-
    generated suggestions. Starters are deliberately excluded: every
    write this module supports (submit_waiver_claim()) assumes the
    dropped player is coming off the bench, since that's the only case
    verified against the real league so far - see that function's
    docstring."""
    rosters = get_trade_rosters(season)
    if rosters.empty:
        return rosters
    mine = rosters[(rosters["team_pk"] == team_pk) & (rosters["is_starter"] == 0)]
    return mine.sort_values("value_score").reset_index(drop=True)


def _espn_team_id_for_pk(team_pk: int) -> int | None:
    row = dd.get_connection().execute("SELECT espn_team_id FROM teams WHERE id = ?", (team_pk,)).fetchone()
    return row["espn_team_id"] if row else None


def get_protected_player_ids(team_pk: int) -> set[int]:
    """Player ids this team's manager has marked "do not drop" -
    get_my_waiver_suggestions() never picks one of these as a suggested
    drop (see that function). Keyed by the team's stable espn_team_id,
    not team_pk - see protected_players.py's module docstring for why."""
    from . import protected_players

    espn_team_id = _espn_team_id_for_pk(team_pk)
    if espn_team_id is None:
        return set()
    return protected_players.load_protected(espn_team_id)


def save_protected_players(team_pk: int, player_ids: set[int]) -> dict:
    """Saves this team's "do not drop" list: writes it locally (takes
    effect immediately for the CURRENT app session/process) and, if
    GITHUB_TOKEN is configured, also commits it to the repo - same
    durability reasoning and pattern as save_rank_override() (see that
    function and config.github_token()'s docstring): a standing "never
    suggest dropping this guy" preference is exactly the kind of thing
    that shouldn't silently vanish the next time Streamlit Cloud wipes
    the app's disk. Returns {"success", "committed", "commit_message"}."""
    from . import protected_players

    espn_team_id = _espn_team_id_for_pk(team_pk)
    if espn_team_id is None:
        return {"success": False, "committed": False, "commit_message": f"Unknown team_pk {team_pk}"}

    path = protected_players.save_protected(espn_team_id, player_ids)

    token = config.github_token()
    if not token:
        return {
            "success": True, "committed": False,
            "commit_message": "GITHUB_TOKEN not configured - saved locally only; it won't survive a redeploy.",
        }

    from . import github_sync

    repo_path = f"protected_players/{espn_team_id}.json"
    result = github_sync.commit_file(
        repo=config.github_repo(), token=token, path=repo_path, content_bytes=path.read_bytes(),
        message=f"Update do-not-drop list for team {espn_team_id}",
    )
    return {"success": True, "committed": result["success"], "commit_message": result["message"]}


# --- Real ESPN waiver-claim write --------------------------------------
#
# Verified against the real league, 2026-09-14, at the user's explicit
# request for a live supervised test ("player and amount don't matter,
# I'll change it right after"). No sandbox exists for this endpoint - two
# live attempts against the real league confirmed the exact payload
# shape: a first try missing `toTeamId`/`fromTeamId` on the ADD/DROP
# items came back as a clean, structured 409 naming the exact missing
# field (`TRAN_ITEM_TO_TEAM_ID_MISSING`); adding them produced a real 200
# with a real PENDING transaction, independently reconfirmed via
# `league.transactions()`. Cancellation was also confirmed (the user
# cancelled that test claim in the ESPN app): shows up as a SEPARATE
# transaction with `executionType: "CANCEL"` and a `relatedTransactionId`
# pointing at the original - the original's own `status` field is never
# rewritten in place (ESPN's transaction log is append-only), so don't
# be alarmed if a cancelled claim still shows `status: PENDING` on its
# own record; the linked CANCEL record is what's authoritative.
#
# `dry_run` defaults to True in every function below - callers (the War
# Room page) must pass dry_run=False explicitly, and only after a human
# has reviewed the exact rendered payload. This sends a REAL transaction
# against REAL FAAB budget with a REAL roster change every time
# dry_run=False actually runs.

WAIVER_WRITE_URL = (
    "https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
    "/segments/0/leagues/{league_id}/transactions/"
)
BENCH_SLOT_ID = 20  # confirmed live 2026-09-14 - ESPN's numeric id for the bench slot
NO_SLOT_ID = -1  # "no lineup slot" - the free-agent-pool side of an ADD/DROP item


def _waiver_claim_payload(
    team_id: int,
    add_player_id: int,
    drop_player_id: int | None,
    bid_amount: float,
    scoring_period: int,
    member_id: str,
) -> dict:
    """Pure payload builder - the exact shape confirmed live (see module
    note above). Only the DROP item assumes BENCH_SLOT_ID as the "from"
    slot - correct for every drop this app ever suggests (get_my_waiver_
    suggestions()/get_droppable_roster() are both bench-only), not
    verified for dropping a current starter."""
    items = [
        {
            "playerId": add_player_id,
            "type": "ADD",
            "fromLineupSlotId": NO_SLOT_ID,
            "toLineupSlotId": BENCH_SLOT_ID,
            "toTeamId": team_id,
        }
    ]
    if drop_player_id is not None:
        items.append(
            {
                "playerId": drop_player_id,
                "type": "DROP",
                "fromLineupSlotId": BENCH_SLOT_ID,
                "toLineupSlotId": NO_SLOT_ID,
                "fromTeamId": team_id,
            }
        )
    return {
        "isLeagueManager": False,
        "teamId": team_id,
        "type": "WAIVER",
        "memberId": member_id,
        "bidAmount": round(bid_amount),  # real FAAB bids are whole dollars - never send our own float noise
        "scoringPeriodId": scoring_period,
        "executionType": "EXECUTE",
        "items": items,
    }


def submit_waiver_claim(
    season: int,
    team_pk: int,
    add_player_id: int,
    drop_player_id: int | None,
    bid_amount: float,
    dry_run: bool = True,
) -> dict:
    """Submit (or, when dry_run, only preview) one real waiver claim.
    Returns {"dry_run", "url", "payload", "success", "status_code",
    "message", "transaction_id"} - "success" is None for a dry run (never
    sent), True/False once a real attempt has actually been made. Never
    raises on an ESPN-side rejection (a clean 409 is an expected, useful
    outcome - see module note) - only a genuine network failure surfaces
    as success=False with the exception text."""
    from .config import load_espn_credentials
    from .espn_client import ESPNClient

    conn = dd.get_connection()
    espn_team_id_row = conn.execute("SELECT espn_team_id FROM teams WHERE id = ?", (team_pk,)).fetchone()
    if not espn_team_id_row:
        return {
            "dry_run": dry_run, "url": None, "payload": None, "success": False,
            "status_code": None, "message": f"Unknown team_pk {team_pk}", "transaction_id": None,
        }

    creds = load_espn_credentials()
    league = ESPNClient().get_league(season)
    scoring_period = league.current_week

    payload = _waiver_claim_payload(
        espn_team_id_row["espn_team_id"], add_player_id, drop_player_id, bid_amount, scoring_period, creds.swid
    )
    url = WAIVER_WRITE_URL.format(season=season, league_id=creds.league_id)

    if dry_run:
        return {
            "dry_run": True, "url": url, "payload": payload, "success": None,
            "status_code": None, "message": "DRY RUN - not sent", "transaction_id": None,
        }

    cookies = {"espn_s2": creds.espn_s2, "SWID": creds.swid}
    try:
        resp = requests.post(url, json=payload, cookies=cookies, headers={"Content-Type": "application/json"}, timeout=20)
    except requests.RequestException as exc:
        return {
            "dry_run": False, "url": url, "payload": payload, "success": False,
            "status_code": None, "message": str(exc), "transaction_id": None,
        }

    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass

    if resp.status_code == 200:
        return {
            "dry_run": False, "url": url, "payload": payload, "success": True,
            "status_code": 200, "message": f"PENDING (transaction {body.get('id')})", "transaction_id": body.get("id"),
        }
    message = "; ".join(body.get("messages") or []) or resp.text[:300] or f"HTTP {resp.status_code}"
    return {
        "dry_run": False, "url": url, "payload": payload, "success": False,
        "status_code": resp.status_code, "message": message, "transaction_id": None,
    }


# --- Lineup optimization (FantasyPros weekly consensus -> ideal lineup) --
#
# Driven by FantasyPros' weekly consensus rankings, NOT a single named
# analyst - see fantasypros_client.fetch_weekly_overall_rankings's
# docstring for the full story: the user specifically wanted Justin
# Boone (Yahoo Fantasy, FantasyPros' #1 overall weekly-accuracy ranker
# as of 2026-09-14, expert_id 317), but (a) FantasyPros' `filters` query
# param for isolating one expert's rankings doesn't actually work
# despite being documented (confirmed live: identical results whether
# filtering to expert 317, a different expert, or nothing at all), and
# (b) he isn't even a registered contributor to FantasyPros' RB/WR/TE
# weekly panels, only QB/K/DST - so a broad weekly consensus (which DOES
# include him for the positions he covers) is what's actually usable.
# Decided with the user, 2026-09-14.
#
# QB+OP (superflex) vs RB/WR/TE+FLEX are two SEPARATE decisions: there's
# no single rank scale comparable across QB and skill positions here
# (FantasyPros' cross-position "ALL" list only covers RB/WR/TE, never
# QB), so QB+OP are simply filled by this team's top-2 QBs by weekly QB
# rank - the standard superflex convention, and consistent with this
# project's own existing QB scarcity premium (metrics/waiver_value.
# position_scarcity_multipliers). A deliberate simplification, not
# silently assumed.


@st.cache_data(ttl=1800)
def get_weekly_flex_rankings(season: int, week: int) -> dict[int, int]:
    """{espn_player_id: cross-position OVERALL weekly rank} for RB/WR/TE,
    from FantasyPros' position=ALL weekly consensus - see module note
    above for why this (not a single named analyst) is what's used. This
    is a rank among ALL skill players league-wide (e.g. the #1 RB might
    be overall rank 3, behind 2 WRs) - NOT the same number as get_weekly_
    positional_ranks()' own-position rank (RB1, RB2, ...) below; both are
    surfaced separately in the Lineup Optimizer table since they answer
    different questions and shouldn't be shown as one ambiguous "rank"
    column (user feedback 2026-09-14). Empty dict on any failure (no API
    key, request error) - callers should treat missing ranks as "no
    signal", never crash the page over it."""
    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    from . import fantasypros_client

    try:
        players = fantasypros_client.fetch_weekly_overall_rankings(api_key, season, week)
    except Exception:  # noqa: BLE001
        return {}
    espn_id_map = get_fp_espn_id_map()
    return {
        espn_id_map[p["player_id"]]: p["rank_ecr"]
        for p in players
        if p.get("player_id") in espn_id_map and p.get("rank_ecr") is not None
    }


# Positions FantasyPros publishes both an own-position weekly rank (e.g.
# "QB6") AND a start_sit_grade (A+..F - their composite matchup-quality
# read, opponent strength + everything else feeding their weekly call,
# the closest signal they publish to a bare opponent-strength number) for
# on their POSITION-SCOPED weekly consensus-rankings endpoint. NOT the
# same rank as get_weekly_flex_rankings()'s cross-position OVERALL number
# above, and the grade isn't present at all on that position=ALL call
# (confirmed live 2026-09-14) - so this is its own position-scoped fetch.
# Includes K/DST too (unlike the optimizer's own qb_pool/skill_pool,
# which never make a start/sit call for them - usually only 1 rostered
# K/D-ST, no real decision to make) purely so the Lineup Optimizer table
# always has a real FantasyPros basis to show/fall back to for them, per
# user feedback 2026-09-14 ("when I submit an override, it sometimes
# won't have D/K/QB rankings - keep the default FantasyPros rankings for
# those positions as basis").
_POSITIONAL_DETAIL_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


@st.cache_data(ttl=1800)
def get_weekly_positional_detail(season: int, week: int) -> dict[int, dict]:
    """{espn_player_id: {"rank": int, "grade": str|None}} - FantasyPros'
    OWN-POSITION weekly rank (QB6, RB14, ... - never a cross-position
    number, see get_weekly_flex_rankings() for that) plus start_sit_grade,
    across QB/RB/WR/TE/K/DST (see _POSITIONAL_DETAIL_POSITIONS note
    above). One call per position - a single position's request failing
    just skips that position rather than blanking the whole dict. Empty
    dict with no FANTASYPROS_API_KEY configured."""
    api_key = config.fantasypros_api_key()
    if not api_key:
        return {}
    from . import fantasypros_client

    espn_id_map = get_fp_espn_id_map()
    detail: dict[int, dict] = {}
    for position in _POSITIONAL_DETAIL_POSITIONS:
        try:
            players = fantasypros_client.fetch_weekly_rankings(api_key, position, season, week)
        except Exception:  # noqa: BLE001 - one position failing shouldn't blank the rest
            continue
        for p in players:
            espn_id = espn_id_map.get(p.get("player_id"))
            if espn_id is None or p.get("rank_ecr") is None:
                continue
            detail[espn_id] = {"rank": p["rank_ecr"], "grade": p.get("start_sit_grade")}
    return detail


def get_weekly_positional_ranks(season: int, week: int) -> dict[int, int]:
    """{espn_player_id: FantasyPros' own-position weekly rank} (QB6,
    RB14, ...) across QB/RB/WR/TE - drives the QB+OP solve directly (see
    build_ideal_lineup; QB has no cross-position list to use instead) and
    is the Lineup Optimizer table's "Positional rank" column for every
    position. Thin wrapper over get_weekly_positional_detail()."""
    return {espn_id: d["rank"] for espn_id, d in get_weekly_positional_detail(season, week).items()}


def get_weekly_matchup_grades(season: int, week: int) -> dict[int, str]:
    """{espn_player_id: FantasyPros start_sit_grade} - the Lineup
    Optimizer table's "Matchup" column. Thin wrapper over
    get_weekly_positional_detail()."""
    return {
        espn_id: d["grade"] for espn_id, d in get_weekly_positional_detail(season, week).items() if d.get("grade")
    }


def get_rank_overrides(season: int, week: int) -> dict[str, int] | None:
    """{normalized_player_name: rank} from an admin-uploaded weekly PDF
    (see rank_override_pdf.py/rank_overrides.py), or None if nothing has
    been uploaded for this week. Not cached (a cheap local file read, and
    callers need to see a just-saved override immediately, not after a
    cache TTL)."""
    from . import rank_overrides

    return rank_overrides.load_override(season, week)


def get_active_rank_override_meta(season: int, week: int) -> dict | None:
    """Raw stored override payload (source_filename/uploaded_at/ranks) for
    the "current override" status the UI shows - None if none active."""
    from . import rank_overrides

    return rank_overrides.load_override_meta(season, week)


def save_rank_override(season: int, week: int, ranks: list[dict], source_filename: str | None) -> dict:
    """Saves an admin-reviewed weekly rank override: writes it locally
    (takes effect immediately for the CURRENT app session/process) and,
    if GITHUB_TOKEN is configured, also commits it to the repo so it (a)
    survives a Streamlit Cloud disk wipe and (b) is visible to the
    Wednesday/Sunday GitHub Actions scripts - a separate environment that
    only ever sees what's actually committed (see config.github_token()'s
    docstring). Returns {"local_path", "committed", "commit_message"} -
    "committed" is False (not an error) when no token is configured, with
    commit_message explaining that the override is session-local only."""
    from . import rank_overrides

    path = rank_overrides.save_override(season, week, ranks, source_filename)

    token = config.github_token()
    if not token:
        return {
            "local_path": str(path), "committed": False,
            "commit_message": "GITHUB_TOKEN not configured - override applied locally only; it won't "
            "survive a redeploy or reach the scheduled GroupMe scripts.",
        }

    from . import github_sync

    repo_path = f"rank_overrides/{season}_wk{week}.json"
    result = github_sync.commit_file(
        repo=config.github_repo(), token=token, path=repo_path, content_bytes=path.read_bytes(),
        message=f"Add Week {week} rank override ({source_filename or 'manual'})",
    )
    return {"local_path": str(path), "committed": result["success"], "commit_message": result["message"]}


def clear_rank_override(season: int, week: int) -> dict:
    """Removes the Week `week` override locally and, if GITHUB_TOKEN is
    configured, also commits the deletion so the scheduled scripts and a
    future redeploy stop seeing it too."""
    from . import rank_overrides

    rank_overrides.clear_override(season, week)

    token = config.github_token()
    if not token:
        return {"committed": False, "commit_message": "GITHUB_TOKEN not configured - cleared locally only."}

    from . import github_sync

    repo_path = f"rank_overrides/{season}_wk{week}.json"
    result = github_sync.delete_file(
        repo=config.github_repo(), token=token, path=repo_path, message=f"Remove Week {week} rank override"
    )
    return {"committed": result["success"], "commit_message": result["message"]}


QB_ELIGIBLE_SLOT_NAMES = {"QB", "OP"}
SKILL_SLOT_COUNT_KEYS = ("RB", "WR", "TE", "RB/WR/TE")
#: Rank-to-value conversion shared by both the QB and skill-pool solves -
#: lower rank = better = higher value; an unranked player (FantasyPros
#: has no opinion, e.g. deep bench/practice-squad-adjacent) floors at 0
#: rather than being excluded outright, so the solver can still legally
#: fill a slot if literally nothing else is eligible.
_RANK_VALUE_CEILING = 10_000


def _value_from_rank(rank: int | None) -> float:
    return max(0.0, _RANK_VALUE_CEILING - rank) if rank is not None else 0.0


def _override_rank_for(player_name: str, overrides: dict[str, int] | None) -> int | None:
    if not overrides:
        return None
    return overrides.get(player_matching.normalize_name(player_name))


def _empty_lineup_result(week: int | None) -> dict:
    return {"changes": [], "zero_projected_starters": [], "lineup_detail": [], "week": week}


def build_ideal_lineup(season: int, team_pk: int) -> dict:
    """Computes this team's ideal starting lineup for the CURRENT live
    week from FantasyPros' weekly consensus rankings (see module note
    above), then metrics.lineup_order to decide which specific slot
    label (base RB/WR/TE vs the RB/WR/TE flex slot) each skill-position
    starter gets, based on real kickoff time (earlier games -> base
    slots, later games -> flex - see that module's docstring for why).

    An admin-uploaded weekly rank override (rank_overrides.py, applied
    via get_rank_overrides()) supersedes FantasyPros' rank for any player
    it covers, matched by normalized name - see rank_overrides.py's
    module docstring for why name matching (not a pre-resolved ESPN id)
    is what makes the same uploaded PDF apply across every league's own
    roster. A player with no override falls back to the plain FantasyPros
    weekly rank, exactly as before.

    A bye-week player (no real game this week) is never placed in a
    starting slot, regardless of rank - they simply can't play.

    Returns {"changes": [{"player_id", "player_name", "position",
    "from_slot", "to_slot", "swap_with_player_id",
    "swap_with_player_name"}], "zero_projected_starters": [{"player_id",
    "player_name", "projected"}], "lineup_detail": [{"player_id",
    "player_name", "position", "current_slot", "proposed_slot",
    "fp_positional_rank", "fp_overall_rank", "override_rank",
    "rank_used", "espn_projected", "opponent", "matchup_grade",
    "injury_status", "on_bye"}], "week": int}.

    "changes" covers every real slot-level move, INCLUDING a player who
    loses their starting spot with no replacement slot of their own (a
    "-> BE" entry that's just as real a change as anyone gaining a slot,
    previously missing entirely). Each entry's swap_with_player_* names
    whoever's moving the other way through the same slot label - who you
    bench to make room, or who's replacing you - so "changes" reads as
    real swaps, not disconnected one-line facts (user feedback
    2026-09-14). An empty list means the current lineup is already
    optimal. zero_projected_starters is computed against the CURRENT
    real lineup (not the proposed one) - a real, live fact independent of
    whether the user acts on the lineup suggestion at all.

    lineup_detail covers EVERY rostered player (starters, bench, IR) -
    the full "what drove this" breakdown the War Room table shows,
    including players the optimizer never considers (K/D-ST, which have
    no cross-position weekly solve here - see module note above).
    fp_positional_rank is FantasyPros' own-position rank (QB6, RB14, ...)
    - fp_overall_rank is their cross-position rank among ALL skill
    players (RB/WR/TE only - no such number exists for QB) - these are
    deliberately two separate fields, not one ambiguous "rank" (user
    feedback 2026-09-14: "1 should be positional rank, 1 should be
    overall rank"). rank_used is whichever of override/positional/
    overall actually drove the optimizer's decision for that player."""
    from .espn_client import ESPNClient
    from .metrics.lineup_order import TimedPlayer, order_flex_pool_by_kickoff

    conn = dd.get_connection()
    espn_team_id_row = conn.execute("SELECT espn_team_id FROM teams WHERE id = ?", (team_pk,)).fetchone()
    if not espn_team_id_row:
        return _empty_lineup_result(None)
    espn_team_id = espn_team_id_row["espn_team_id"]

    league = ESPNClient().get_league(season)
    week = league.current_week
    box_scores = league.box_scores(week)
    lineup = None
    for bs in box_scores:
        if bs.home_team and bs.home_team.team_id == espn_team_id:
            lineup = bs.home_lineup
        elif bs.away_team and bs.away_team.team_id == espn_team_id:
            lineup = bs.away_lineup
    if lineup is None:
        return _empty_lineup_result(week)

    zero_projected_starters = [
        {"player_id": bp.playerId, "player_name": bp.name, "projected": bp.projected_points}
        for bp in lineup
        if bp.slot_position not in ("BE", "IR")
        and (bp.projected_points or 0) == 0
        and not getattr(bp, "on_bye_week", False)
    ]

    # overall_ranks: cross-position rank among ALL skill players (RB/WR/TE
    # only - FantasyPros has no cross-position list that includes QB, see
    # module note above) - what actually drives the skill-pool solve.
    # positional_ranks: each player's OWN-position rank (QB6, RB14, ...)
    # across QB/RB/WR/TE - drives the QB+OP solve directly (QB has no
    # overall number to use instead) and is shown as its own column
    # alongside overall_ranks for skill positions, since the two answer
    # different questions and were previously conflated into one
    # ambiguous "fp_rank" (user feedback 2026-09-14: "FantasyPros rank -
    # 1 should be positional rank, 1 should be overall rank").
    overall_ranks = get_weekly_flex_rankings(season, week)
    positional_ranks = get_weekly_positional_ranks(season, week)
    matchup_grades = get_weekly_matchup_grades(season, week)
    overrides = get_rank_overrides(season, week)
    slot_counts = get_position_slot_counts(season)

    playable = [
        bp for bp in lineup
        if not getattr(bp, "on_bye_week", False) and getattr(bp, "game_date", None) is not None
    ]
    current_slot_by_id = {bp.playerId: bp.slot_position for bp in lineup}
    name_by_id = {bp.playerId: bp.name for bp in lineup}
    position_by_id = {bp.playerId: bp.position for bp in lineup}

    # Classify by "QB" eligibility specifically, NOT QB_ELIGIBLE_SLOT_NAMES
    # (QB+OP) - confirmed live 2026-09-14 that this league's real OP slot
    # is a true any-position flex (every RB/WR/TE here is ALSO OP-eligible,
    # not just QBs, so this isn't actually a superflex league), so
    # OP-eligibility alone can't distinguish a QB from a skill player. Real
    # quarterbacks are the only players with "QB" itself in eligibleSlots -
    # using that (not the shared OP tag) is what keeps every skill player
    # correctly routed into skill_pool instead of being silently absorbed
    # into qb_pool (where they'd have no QB rank and never be considered
    # for RB/WR/TE/FLEX at all - a real bug this fixes, not a redesign of
    # the documented "top-2-QBs-fill-QB+OP" simplification below, which
    # is unaffected).
    qb_pool = [bp for bp in playable if "QB" in bp.eligibleSlots]
    skill_pool = [bp for bp in playable if "QB" not in bp.eligibleSlots and set(SKILL_SLOT_COUNT_KEYS) & set(bp.eligibleSlots)]
    qb_pool_ids = {bp.playerId for bp in qb_pool}
    skill_pool_ids = {bp.playerId for bp in skill_pool}

    def _fp_positional_rank_for(bp) -> int | None:
        return positional_ranks.get(bp.playerId)

    def _fp_overall_rank_for(bp) -> int | None:
        # Only meaningful for skill positions - FantasyPros has no true
        # cross-position rank that includes QB (see module note above).
        return overall_ranks.get(bp.playerId) if bp.playerId in skill_pool_ids else None

    def _rank_used_for(bp) -> int | None:
        # Override always wins when present for this player. Otherwise:
        # skill_pool players fall back to their cross-position overall
        # rank (what actually drives their solve decision); everyone
        # else - QB, and K/D-ST (which have no solve decision to drive at
        # all, but still deserve a real basis to SHOW - user feedback
        # 2026-09-14) - falls back to their own-position rank, since
        # there's no overall-rank concept for them anyway.
        override_rank = _override_rank_for(bp.name, overrides)
        if override_rank is not None:
            return override_rank
        if bp.playerId in skill_pool_ids:
            return _fp_overall_rank_for(bp)
        return _fp_positional_rank_for(bp)

    proposed_slot_by_id: dict[int, str] = {}

    qb_slot_counts = {k: v for k, v in slot_counts.items() if k in QB_ELIGIBLE_SLOT_NAMES and v}
    if qb_pool and qb_slot_counts:
        qb_roster_players = [
            RosterPlayer(
                player_id=bp.playerId, points=_value_from_rank(_rank_used_for(bp)),
                eligible_slots=frozenset(bp.eligibleSlots),
            )
            for bp in qb_pool
        ]
        _, qb_assignment = optimal_lineup(qb_roster_players, qb_slot_counts)
        for label, player_id in qb_assignment.items():
            proposed_slot_by_id[player_id] = label.split("#")[0]

    skill_slot_counts = {k: v for k, v in slot_counts.items() if k in SKILL_SLOT_COUNT_KEYS and v}
    if skill_pool and skill_slot_counts:
        skill_roster_players = [
            RosterPlayer(
                player_id=bp.playerId, points=_value_from_rank(_rank_used_for(bp)),
                eligible_slots=frozenset(bp.eligibleSlots),
            )
            for bp in skill_pool
        ]
        _, skill_assignment = optimal_lineup(skill_roster_players, skill_slot_counts)
        chosen_ids = set(skill_assignment.values())
        chosen_players = [
            TimedPlayer(player_id=bp.playerId, eligible_slots=frozenset(bp.eligibleSlots), kickoff=bp.game_date)
            for bp in skill_pool
            if bp.playerId in chosen_ids
        ]
        proposed_slot_by_id.update(order_flex_pool_by_kickoff(chosen_players, skill_slot_counts))

    # Every real slot-level move: current -> proposed for anyone proposed
    # to start somewhere different, PLUS an explicit "-> BE" move for
    # anyone CURRENTLY starting who isn't proposed to start ANYWHERE
    # (previously invisible - proposed_slot_by_id only ever contains
    # players the solver chose to start, so a player who loses their
    # spot without a replacement slot of their own never showed up as a
    # change at all, even though a real demotion happened). Scoped to
    # qb_pool_ids | skill_pool_ids ONLY - a currently-starting K/D-ST (or
    # anyone else outside both pools) was never evaluated by the solver
    # at all, so their absence from proposed_slot_by_id isn't a real
    # demotion, just "not this feature's scope" (see module note above).
    current_starter_ids = {
        bp.playerId for bp in lineup
        if bp.slot_position not in ("BE", "IR") and bp.playerId in (qb_pool_ids | skill_pool_ids)
    }
    proposed_starter_ids = set(proposed_slot_by_id.keys())
    moves: list[tuple[int, str, str]] = [
        (player_id, current_slot_by_id[player_id], to_slot)
        for player_id, to_slot in proposed_slot_by_id.items()
        if current_slot_by_id.get(player_id) not in (None, to_slot)
    ]
    moves += [
        (player_id, current_slot_by_id[player_id], "BE")
        for player_id in current_starter_ids - proposed_starter_ids
    ]

    # Pair each move with whoever's swapping the other way through the
    # SAME slot label, so "changes" reads as real swaps ("who do we bench
    # to make room") instead of disconnected one-line facts (user
    # feedback 2026-09-14). A slot's total seat count never changes, so
    # the players leaving a label and the players entering it always
    # match up 1:1 - pairing both queues per label (rather than two
    # independent lookups) keeps a mutual swap's two lines pointing at
    # each other consistently.
    leaving_by_slot: dict[str, list[int]] = {}
    entering_by_slot: dict[str, list[int]] = {}
    for player_id, from_slot, to_slot in moves:
        leaving_by_slot.setdefault(from_slot, []).append(player_id)
        entering_by_slot.setdefault(to_slot, []).append(player_id)
    swap_partner_by_id: dict[int, int] = {}
    for slot_label in set(leaving_by_slot) | set(entering_by_slot):
        for leaver, enterer in zip(leaving_by_slot.get(slot_label, []), entering_by_slot.get(slot_label, [])):
            swap_partner_by_id[leaver] = enterer
            swap_partner_by_id[enterer] = leaver

    changes = [
        {
            "player_id": player_id, "player_name": name_by_id.get(player_id),
            "position": position_by_id.get(player_id), "from_slot": from_slot, "to_slot": to_slot,
            "swap_with_player_id": swap_partner_by_id.get(player_id),
            "swap_with_player_name": name_by_id.get(swap_partner_by_id.get(player_id)),
        }
        for player_id, from_slot, to_slot in moves
    ]

    lineup_detail = []
    for bp in lineup:
        fp_positional_rank = _fp_positional_rank_for(bp)
        fp_overall_rank = _fp_overall_rank_for(bp)
        override_rank = _override_rank_for(bp.name, overrides)
        injury_status = getattr(bp, "injuryStatus", None)
        if not isinstance(injury_status, str):
            injury_status = None  # D/ST has no real injury status (espn_api returns [] there)
        # None when the solver's proposed slot MATCHES the current one
        # (no real change - e.g. McCaffrey staying at RB) rather than
        # repeating the same value in both columns, which read as if a
        # change were being suggested when none was (user feedback
        # 2026-09-14). A genuinely benched-with-no-slot player already
        # gets None here too (never entered proposed_slot_by_id at all).
        raw_proposed_slot = proposed_slot_by_id.get(bp.playerId)
        proposed_slot = raw_proposed_slot if raw_proposed_slot != bp.slot_position else None
        lineup_detail.append(
            {
                "player_id": bp.playerId,
                "player_name": bp.name,
                "position": bp.position,
                "current_slot": bp.slot_position,
                "proposed_slot": proposed_slot,
                "fp_positional_rank": fp_positional_rank,
                "fp_overall_rank": fp_overall_rank,
                "override_rank": override_rank,
                "rank_used": _rank_used_for(bp),
                "espn_projected": bp.projected_points,
                "opponent": getattr(bp, "pro_opponent", None),
                "matchup_grade": matchup_grades.get(bp.playerId),
                "injury_status": injury_status,
                "on_bye": bool(getattr(bp, "on_bye_week", False)),
            }
        )

    return {"changes": changes, "zero_projected_starters": zero_projected_starters, "lineup_detail": lineup_detail, "week": week}


def slot_name_to_id(slot_name: str) -> int:
    """ESPN's numeric lineup slot id for a slot name string (e.g. "TE"
    -> 6, "RB/WR/TE" -> 23, "BE" -> 20) - build_ideal_lineup works in
    slot name strings throughout (matching league.settings.
    position_slot_counts' own key naming and espn_api's Player.
    eligibleSlots), so this is the one place that needs the numeric ids
    the real write endpoint requires.

    espn_api's own POSITION_MAP is a single dict merging BOTH directions
    (int id -> name AND name -> int id), but its name-keyed side is
    incomplete and inconsistent with the numeric side for exactly the
    slots this feature actually needs: it has no entry at all for "BE"/
    "IR"/"OP" (only their reverse int->name entries exist), and slot 23
    is keyed "FLEX" there instead of "RB/WR/TE" (confirmed live
    2026-09-14 - a real bug this surfaced: `POSITION_MAP["RB/WR/TE"]`
    KeyErrors, breaking every real flex-slot lineup submission). The
    int-keyed side of the SAME dict is complete and DOES use "RB/WR/TE"
    consistently with this project's own naming - so this inverts that
    side rather than trusting the name-keyed side directly."""
    from espn_api.football.constant import POSITION_MAP

    name_to_id = {name: slot_id for slot_id, name in POSITION_MAP.items() if isinstance(slot_id, int)}
    return name_to_id[slot_name]


LINEUP_WRITE_URL = WAIVER_WRITE_URL  # same transactions endpoint, different "type"/item shape


def _lineup_change_payload(team_id: int, moves: list[tuple], scoring_period: int, member_id: str) -> dict:
    """moves: list of (player_id, from_slot_id, to_slot_id) - NUMERIC
    ESPN slot ids (see espn_api.football.constant.POSITION_MAP), not
    slot name strings. Pure payload builder - confirmed live 2026-09-14
    that a single lineup slot change goes through the SAME transactions
    endpoint as a waiver claim, but type="ROSTER" with item type=
    "LINEUP" (fromTeamId/toTeamId both the player's own team, since
    nobody's roster membership changes, just their slot). Multiple
    simultaneous LINEUP items in one call were NOT separately live-
    verified (only a single-item change was, twice) - the waiver-claim
    endpoint already proved multi-item transactions work in general
    (ADD+DROP together), so this is a reasonable extrapolation, but
    worth one supervised live test before depending on it for a real
    Sunday lineup set."""
    return {
        "isLeagueManager": False,
        "teamId": team_id,
        "type": "ROSTER",
        "memberId": member_id,
        "scoringPeriodId": scoring_period,
        "executionType": "EXECUTE",
        "items": [
            {
                "playerId": player_id, "type": "LINEUP",
                "fromLineupSlotId": from_slot_id, "toLineupSlotId": to_slot_id,
                "fromTeamId": team_id, "toTeamId": team_id,
            }
            for player_id, from_slot_id, to_slot_id in moves
        ],
    }


def submit_lineup_changes(season: int, team_pk: int, moves: list[tuple], dry_run: bool = True) -> dict:
    """Submit (or, when dry_run, only preview) a batch of real lineup
    slot changes in ONE transaction. moves: list of (player_id,
    from_slot_id, to_slot_id) NUMERIC ESPN slot ids. Same real/dry-run
    semantics and return shape as submit_waiver_claim - defaults to
    dry_run=True, never sends anything unless a caller explicitly passes
    dry_run=False after a human has reviewed the exact rendered
    payload."""
    from .config import load_espn_credentials
    from .espn_client import ESPNClient

    conn = dd.get_connection()
    espn_team_id_row = conn.execute("SELECT espn_team_id FROM teams WHERE id = ?", (team_pk,)).fetchone()
    if not espn_team_id_row:
        return {
            "dry_run": dry_run, "url": None, "payload": None, "success": False,
            "status_code": None, "message": f"Unknown team_pk {team_pk}", "transaction_id": None,
        }

    creds = load_espn_credentials()
    league = ESPNClient().get_league(season)
    scoring_period = league.current_week

    payload = _lineup_change_payload(espn_team_id_row["espn_team_id"], moves, scoring_period, creds.swid)
    url = LINEUP_WRITE_URL.format(season=season, league_id=creds.league_id)

    if dry_run:
        return {
            "dry_run": True, "url": url, "payload": payload, "success": None,
            "status_code": None, "message": "DRY RUN - not sent", "transaction_id": None,
        }

    cookies = {"espn_s2": creds.espn_s2, "SWID": creds.swid}
    try:
        resp = requests.post(url, json=payload, cookies=cookies, headers={"Content-Type": "application/json"}, timeout=20)
    except requests.RequestException as exc:
        return {
            "dry_run": False, "url": url, "payload": payload, "success": False,
            "status_code": None, "message": str(exc), "transaction_id": None,
        }

    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass

    if resp.status_code == 200:
        return {
            "dry_run": False, "url": url, "payload": payload, "success": True,
            "status_code": 200, "message": f"PENDING (transaction {body.get('id')})", "transaction_id": body.get("id"),
        }
    message = "; ".join(body.get("messages") or []) or resp.text[:300] or f"HTTP {resp.status_code}"
    return {
        "dry_run": False, "url": url, "payload": payload, "success": False,
        "status_code": resp.status_code, "message": message, "transaction_id": None,
    }
