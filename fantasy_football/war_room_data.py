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
        fp_ros_points_by_espn_id = {}
        for fp in fp_players:
            espn_id = id_map.get(fp["player_id"])
            if espn_id is not None:
                fp_pos_rank_by_espn_id[espn_id] = player_matching.parse_pos_rank(fp.get("pos_rank"))
                fp_ros_points_by_espn_id[espn_id] = fp.get("r2p_pts")

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

    suggestions: list[dict] = []
    per_position_count: dict[str, int] = {}
    ranked = board[board["suggested_bid"].notna()].sort_values("suggested_bid", ascending=False)
    for _, fa in ranked.iterrows():
        position = fa["position"]
        if per_position_count.get(position, 0) >= MAX_SUGGESTIONS_PER_POSITION:
            continue

        same_position_bench = bench[bench["position"] == position]
        if not same_position_bench.empty:
            drop_candidate = same_position_bench.iloc[0]
        elif not bench.empty:
            drop_candidate = bench.iloc[0]
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
