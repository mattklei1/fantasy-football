#!/usr/bin/env python3
"""One-off: find the league's real recent TRADE_ACCEPT transactions (from
the `transactions` table ingest_recent_activity already populates - see
ingest.py), grade each one for BOTH sides using the same marginal
starting-lineup-value engine the War Room Trade Evaluator uses
(war_room_data.evaluate_trade), and measure each involved team's
resulting championship-odds swing via the same counterfactual
re-simulation technique used elsewhere in this project (flip ONE input -
here, which team a traded player sits on - hold everything else fixed,
re-run metrics.playoff_sim.simulate_season, diff the output).

Read-only analysis - writes nothing, sends nothing. Prints everything to
this run's log for review.

Usage: python scripts/grade_trades.py
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import db, war_room_data
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics import loaders as metric_loaders
from fantasy_football.metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from fantasy_football.metrics.pipeline import compute_and_store_lineup_efficiency, compute_and_store_season_metrics
from fantasy_football.metrics.playoff_sim import simulate_season
from fantasy_football.metrics.roster_strength import (
    compute_player_values,
    compute_team_roster_strength,
    roster_strength_to_points,
)

SIM_SEED = 42  # same seed for the with-trade and without-trade run - common
# random numbers, so the only thing that can move championship_pct between
# the two runs is the shrinkage_prior swap itself, not fresh simulation noise.

RECENT_DAYS = 5  # ESPN's recent-activity feed has no season-start cutoff of
# its own (ingest_recent_activity pulls a rolling window of up to 1000
# records, which can reach back well over a month in an active league) -
# the user asked about trades from "the last few days," so trades older
# than this are found but not graded.

# A trade ESPN has ACCEPTED but not yet PROCESSED never shows up in
# ingest_recent_activity's TRADE_SENT/TRADE_RECEIVED feed - that feed is
# populated once the roster move actually lands, not at acceptance (user,
# 2026-11-13, supplying this from the real "Trade Accepted" notification:
# "Here it is. Accepted but not processed"). Supplied by hand below
# (team names + real player names, resolved against this run's live
# players/teams tables) rather than guessed at from any ESPN endpoint -
# no live signal for a merely-accepted trade was found to be reliable.
PENDING_TRADES = [
    {
        "date": "2026-11-12T20:11:00",
        "legs": [
            ("Snacks and Waddles Nursery", ["Luther Burden III", "Puka Nacua"]),
            ("Robbying Moore TDs", ["Tank Dell", "Jared Goff", "Carnell Tate"]),
        ],
    },
]


def _group_trades(conn: sqlite3.Connection, season: int) -> dict[str, list[tuple]]:
    """activity_date -> list of (team_pk, action_type, player_id, player_name)
    rows, for every TRADE_SENT/TRADE_RECEIVED leg this ingest run pulled
    from ESPN's real recent-activity feed (see ingest.ingest_recent_activity -
    already wired into ingest_season for the current season). One
    activity_date == one real trade event (ESPN bundles every player leg
    of one trade under a single activity timestamp)."""
    rows = conn.execute(
        """
        SELECT activity_date, team_pk, action_type, player_id, player_name
        FROM transactions
        WHERE season_id = ? AND action_type IN ('TRADE_SENT', 'TRADE_RECEIVED')
        ORDER BY activity_date
        """,
        (season,),
    ).fetchall()
    trades: dict[str, list[tuple]] = {}
    for activity_date, team_pk, action_type, player_id, player_name in rows:
        trades.setdefault(activity_date, []).append((team_pk, action_type, player_id, player_name))
    return trades


def _sides(legs: list[tuple]) -> dict:
    """Resolves one trade's legs into {team_pk: [player_ids sent away]} -
    the shape war_room_data.evaluate_trade() needs. Only handles the
    standard 2-team case (every real trade this league has ever run)."""
    sent = {(pk, pid): name for pk, atype, pid, name in legs if atype == "TRADE_SENT"}
    out_by_team: dict[int, list[int]] = {}
    names_by_id: dict[int, str] = {}
    for (pk, pid), name in sent.items():
        out_by_team.setdefault(pk, []).append(pid)
        names_by_id[pid] = name
    return out_by_team, names_by_id


def _resolve_pending_trades(conn: sqlite3.Connection, season: int) -> dict[str, list[tuple]]:
    """Resolves PENDING_TRADES (team names + player names) against this
    run's real live teams/players tables, into the same activity_date ->
    legs shape _group_trades() produces, so both sources merge into one
    trades dict downstream. Raises if a name doesn't match anything real -
    silently dropping a leg would silently under-grade a real trade."""
    team_pk_by_name = {
        name: pk for pk, name in conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,))
    }
    resolved: dict[str, list[tuple]] = {}
    for trade in PENDING_TRADES:
        legs = []
        for team_name, player_names in trade["legs"]:
            team_pk = team_pk_by_name.get(team_name)
            if team_pk is None:
                raise ValueError(f"PENDING_TRADES: no real team named {team_name!r} in season {season}")
            for player_name in player_names:
                row = conn.execute(
                    "SELECT player_id, player_name FROM players WHERE player_name = ?", (player_name,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"PENDING_TRADES: no real player named {player_name!r} found")
                player_id, real_name = row
                legs.append((team_pk, "TRADE_SENT", player_id, real_name))
        resolved[trade["date"]] = legs
    return resolved


def _swap_team(df: pd.DataFrame, player_ids: list[int], new_team_pk: int) -> pd.DataFrame:
    out = df.copy()
    out.loc[out["player_id"].isin(player_ids), "team_pk"] = new_team_pk
    return out


def _at_origin(df: pd.DataFrame, out_a: list[int], team_a_pk: int, out_b: list[int], team_b_pk: int) -> pd.DataFrame:
    """Every traded player assigned to the team that SENT them away - the
    real pre-trade roster, constructed explicitly rather than inferred
    from the DB's current state. Deliberately NOT just "whatever the DB
    currently shows" - ESPN trades go through an acceptance step
    (visible immediately in the recent-activity feed, which is how this
    script finds the trade at all) followed by a separate processing
    delay before the roster move actually lands, so a just-accepted
    trade can sit with both players still on their ORIGINAL team in
    weekly_rosters for a day or more. Building both hypothetical states
    explicitly (see _at_destination) makes this script correct either
    way, with no need to check which side of that delay a given trade
    happens to be on."""
    out = _swap_team(df, out_a, team_a_pk)
    return _swap_team(out, out_b, team_b_pk)


def _at_destination(df: pd.DataFrame, out_a: list[int], team_b_pk: int, out_b: list[int], team_a_pk: int) -> pd.DataFrame:
    """Every traded player assigned to the team that RECEIVED them - the
    real post-trade roster once ESPN finishes processing it. See
    _at_origin's docstring."""
    out = _swap_team(df, out_a, team_b_pk)
    return _swap_team(out, out_b, team_a_pk)


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = sqlite3.connect(db.DB_PATH)
        db.init_db(conn)
        print("Ingesting real league data (rosters, rankings, recent activity)...")
        ingest_season(conn, client, season, log=print)
        compute_and_store_season_metrics(conn, season)
        compute_and_store_lineup_efficiency(conn, season)

        team_names = dict(conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,)).fetchall())

        all_trades = _group_trades(conn, season)
        pending = _resolve_pending_trades(conn, season)
        for date, legs in pending.items():
            print(f"[manual] resolved pending trade on {date} against live teams/players")
        all_trades.update(pending)

        print(f"\nFull trade history found ({len(all_trades)} event(s)):")
        for d, legs in sorted(all_trades.items()):
            out_by_team_all, names_by_id_all = _sides(legs)
            parts = [
                f"{team_names.get(pk, pk)} sent {[names_by_id_all[p] for p in pids]}"
                for pk, pids in out_by_team_all.items()
            ]
            print(f"  {d}: " + " | ".join(parts))
        print()

        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=RECENT_DAYS)
        trades = {d: legs for d, legs in all_trades.items() if datetime.datetime.fromisoformat(d) >= cutoff}
        print(
            f"Found {len(all_trades)} total trade event(s) (activity feed + manual); "
            f"{len(trades)} within the last {RECENT_DAYS} days.\n"
        )
        if not trades:
            print("No trades within the recent window - nothing to grade.")
            return 0

        meta = conn.execute(
            "SELECT current_week, reg_season_count, position_slot_counts, median_scoring, playoff_team_count "
            "FROM seasons WHERE season_id = ?",
            (season,),
        ).fetchone()
        current_week, reg_season_count, position_slot_counts_json, median_scoring, playoff_team_count = meta
        position_slot_counts = json.loads(position_slot_counts_json)

        rosters_df = war_room_data.get_trade_rosters(season)
        team_state, remaining_matchups = metric_loaders.load_playoff_sim_state(conn, season)

        raw_week_rows = pd.read_sql_query(
            """
            SELECT wr.team_pk, wr.player_id, p.default_position AS position,
                   wr.slot_position, wr.is_starter, wr.eligible_slots,
                   pws.projected_points, fpr.pos_rank AS fp_pos_rank
            FROM weekly_rosters wr
            JOIN players p ON p.player_id = wr.player_id
            LEFT JOIN player_week_scores pws
                ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
            LEFT JOIN fantasypros_rankings fpr
                ON fpr.season_id = wr.season_id AND fpr.week = wr.week AND fpr.player_id = wr.player_id
            WHERE wr.season_id = ? AND wr.week = ?
            """,
            conn, params=(season, current_week),
        )
        raw_week_rows["eligible_slots"] = raw_week_rows["eligible_slots"].apply(
            lambda s: frozenset(json.loads(s)) if pd.notna(s) else frozenset()
        )
        raw_week_rows["projected_points"] = raw_week_rows["projected_points"].fillna(0.0)

        def _optimal_points_all(df: pd.DataFrame) -> pd.Series:
            pts = {}
            for team_pk, g in df.groupby("team_pk"):
                players = [
                    RosterPlayer(int(r.player_id), float(r.projected_points), r.eligible_slots)
                    for r in g.itertuples()
                ]
                total, _ = optimal_lineup(players, position_slot_counts)
                pts[int(team_pk)] = total
            return pd.Series(pts)

        team_pks_sorted = sorted(raw_week_rows["team_pk"].unique())

        def _shrinkage_prior(rows: pd.DataFrame) -> pd.Series:
            strength = compute_team_roster_strength(
                compute_player_values(rows), week=current_week, reg_season_count=reg_season_count
            ).set_index("team_pk")["roster_strength"]
            optimal_points = _optimal_points_all(rows)
            return roster_strength_to_points(
                strength.reindex(team_pks_sorted), optimal_points.reindex(team_pks_sorted)
            )

        for date, legs in trades.items():
            out_by_team, names_by_id = _sides(legs)
            team_pks = list(out_by_team.keys())
            if len(team_pks) != 2:
                print(f"=== Trade on {date}: skipping - not a standard 2-team trade (teams: {team_pks}) ===\n")
                continue
            team_a_pk, team_b_pk = team_pks
            team_a_name, team_b_name = team_names.get(team_a_pk), team_names.get(team_b_pk)
            out_a, out_b = out_by_team[team_a_pk], out_by_team[team_b_pk]

            print(f"=== TRADE on {date}: {team_a_name} <-> {team_b_name} ===")
            print(f"{team_a_name} sent: {[names_by_id[p] for p in out_a]}")
            print(f"{team_b_name} sent: {[names_by_id[p] for p in out_b]}")

            # --- Trade grade: marginal starting-lineup value, both sides ---
            pre_trade_rosters = rosters_df.copy()
            pre_trade_rosters.loc[pre_trade_rosters["player_id"].isin(out_a), ["team_pk", "team_name"]] = [
                team_a_pk, team_a_name
            ]
            pre_trade_rosters.loc[pre_trade_rosters["player_id"].isin(out_b), ["team_pk", "team_name"]] = [
                team_b_pk, team_b_name
            ]
            grade = war_room_data.evaluate_trade(
                pre_trade_rosters, team_a_name, team_b_name, out_a, out_b, position_slot_counts
            )
            for team_name, side in grade.items():
                print(
                    f"  {team_name}: optimal-lineup value {side['before']:.1f} -> {side['after']:.1f} "
                    f"(gain {side['gain']:+.1f}); newly starting: {side['newly_starting'] or '-'}; "
                    f"newly benched: {side['newly_benched'] or '-'}"
                )

            # --- Championship-odds impact: counterfactual re-simulation ---
            # Both states are built explicitly (origin vs destination), not
            # inferred from the DB's current roster snapshot - see
            # _at_origin's docstring for why that matters for a trade
            # that's accepted but still mid-processing on ESPN's side.
            before_rows = _at_origin(raw_week_rows, out_a, team_a_pk, out_b, team_b_pk)
            after_rows = _at_destination(raw_week_rows, out_a, team_b_pk, out_b, team_a_pk)
            shrinkage_before_all = _shrinkage_prior(before_rows)
            shrinkage_after_all = _shrinkage_prior(after_rows)

            prior_after = shrinkage_after_all.reindex(team_state["team_pk"]).to_numpy()
            prior_before = shrinkage_before_all.reindex(team_state["team_pk"]).to_numpy()

            print(
                f"  [debug] shrinkage prior (pts): {team_a_name} "
                f"{shrinkage_before_all.get(team_a_pk):.1f} -> {shrinkage_after_all.get(team_a_pk):.1f}; "
                f"{team_b_name} {shrinkage_before_all.get(team_b_pk):.1f} -> {shrinkage_after_all.get(team_b_pk):.1f}"
            )
            names = dict(conn.execute("SELECT player_id, player_name FROM players").fetchall())
            for pk, name in ((team_a_pk, team_a_name), (team_b_pk, team_b_name)):
                detail = compute_player_values(after_rows[after_rows["team_pk"] == pk])
                print(f"  [debug] {name} post-trade roster (week {current_week}):")
                for r in detail.sort_values("is_starter", ascending=False).itertuples():
                    print(
                        f"    {names.get(r.player_id, r.player_id):<24} pos={r.position:<4} "
                        f"starter={int(r.is_starter)} proj_pts={r.projected_points:.1f} "
                        f"fp_pos_rank={r.fp_pos_rank} player_value={r.player_value:.2f}"
                    )

            sim_after = simulate_season(
                team_state, remaining_matchups, bool(median_scoring), reg_season_count,
                playoff_team_count=playoff_team_count, shrinkage_prior=prior_after,
                rng=np.random.default_rng(SIM_SEED),
            ).set_index("team_pk")
            sim_before = simulate_season(
                team_state, remaining_matchups, bool(median_scoring), reg_season_count,
                playoff_team_count=playoff_team_count, shrinkage_prior=prior_before,
                rng=np.random.default_rng(SIM_SEED),
            ).set_index("team_pk")

            for pk, name in ((team_a_pk, team_a_name), (team_b_pk, team_b_name)):
                champ_before = 100 * sim_before.loc[pk, "championship_pct"]
                champ_after = 100 * sim_after.loc[pk, "championship_pct"]
                playoff_before = 100 * sim_before.loc[pk, "playoff_pct"]
                playoff_after = 100 * sim_after.loc[pk, "playoff_pct"]
                print(
                    f"  {name} championship odds: {champ_before:.1f}% -> {champ_after:.1f}% "
                    f"({champ_after - champ_before:+.1f} pts); playoff odds: {playoff_before:.1f}% -> "
                    f"{playoff_after:.1f}% ({playoff_after - playoff_before:+.1f} pts)"
                )
            print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
