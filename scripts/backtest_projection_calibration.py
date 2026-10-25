#!/usr/bin/env python3
"""One-off analysis script (not scheduled - run via workflow_dispatch):
backtests this season's real projection systems against what actually
happened, so far this season, and prints a full report.

Part 1 - weekly score range (p10/p90) + median-cutline probability
calibration (metrics/cutline_sim.py): for every real completed week,
reconstructs what the model would have said BEFORE that week's games
(pregame optimal-lineup projection as each team's mean - real roster +
real ESPN pregame player projections for that exact week, see
metrics/loaders.load_optimal_lineup_points; each team's own score
stdev computed from ONLY strictly earlier weeks, so there is no
lookahead), then compares to the real score each team actually put up.

Part 2 - playoff-odds week-to-week swings: reads the real, already-
committed playoff_odds_snapshots/{season}.json manifest (final computed
values, captured once per week, never recomputed - no lookahead risk
there by construction) and measures how much each team's playoff_pct
moved week over week, decomposed into "driven by that week's real game
result" vs. "driven by everything else that also changed between
snapshots" (roster-strength prior drift from waiver moves, injury news,
FantasyPros rank changes, etc. - not the actual game being played).

Usage: python scripts/backtest_projection_calibration.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import ingest_season
from fantasy_football.metrics import loaders as metric_loaders
from fantasy_football.metrics.cutline_sim import TeamScoreModel, percentile_range, simulate_cutline_probabilities
from fantasy_football.metrics.pipeline import compute_and_store_season_metrics
from fantasy_football.metrics.playoff_sim import SUPPORTED_PLAYOFF_TEAM_COUNT, compute_score_stdev, simulate_season
from fantasy_football.metrics.win_probability import MIN_STDEV

N_SIMS = 5000


def backtest_score_range_and_cutline(conn, season, last_completed_week, position_slot_counts, median_scoring):
    print("=" * 70)
    print("PART 1: WEEKLY SCORE RANGE (p10/p90) + MEDIAN CUTLINE CALIBRATION")
    print("=" * 70)

    names_df = pd.read_sql_query(
        "SELECT id AS team_pk, team_name FROM teams WHERE season_id = ?", conn, params=(season,)
    )
    names = dict(zip(names_df["team_pk"], names_df["team_name"]))

    rows = []
    for week in range(1, last_completed_week + 1):
        mean_by_team = metric_loaders.load_optimal_lineup_points(conn, season, week, position_slot_counts)
        if mean_by_team.empty:
            print(f"week {week}: no pregame roster/projection data - skipping")
            continue

        prior_scores = pd.read_sql_query(
            "SELECT team_pk, score FROM weekly_team_scores WHERE season_id = ? AND week < ? AND completed = 1",
            conn, params=(season, week),
        )
        if week == 1 or prior_scores.empty:
            stdev_by_team = pd.Series(MIN_STDEV, index=mean_by_team.index)
        else:
            stdev_by_team = prior_scores.groupby("team_pk")["score"].std(ddof=1).clip(lower=MIN_STDEV)
            stdev_by_team = stdev_by_team.reindex(mean_by_team.index).fillna(MIN_STDEV)

        actual = pd.read_sql_query(
            "SELECT team_pk, score FROM weekly_team_scores WHERE season_id = ? AND week = ? AND completed = 1",
            conn, params=(season, week),
        ).set_index("team_pk")["score"]
        common = mean_by_team.index.intersection(stdev_by_team.index).intersection(actual.index)
        if len(common) < 2:
            print(f"week {week}: fewer than 2 teams with complete data - skipping")
            continue

        teams = [
            TeamScoreModel(team_pk=int(pk), mean=float(mean_by_team[pk]), stdev=float(stdev_by_team[pk]))
            for pk in common
        ]
        p_making_it = simulate_cutline_probabilities(teams, n_sims=N_SIMS)

        week_actual = actual.reindex(common)
        median_actual = week_actual.median()
        n_teams = len(common)
        cutoff_rank = n_teams // 2
        ranks = week_actual.rank(ascending=False, method="first")
        actually_made_it = {int(pk): bool(ranks[pk] <= cutoff_rank) for pk in common}

        for t in teams:
            p10, p90 = percentile_range(t)
            a = float(actual[t.team_pk])
            rows.append(
                {
                    "week": week, "team_pk": t.team_pk, "team": names.get(t.team_pk, "?"),
                    "mean": t.mean, "stdev": t.stdev, "p10": p10, "p90": p90, "actual": a,
                    "in_range": p10 <= a <= p90,
                    "z": (a - t.mean) / t.stdev if t.stdev > 0 else 0.0,
                    "p_making_it": p_making_it[t.team_pk], "actually_made_it": actually_made_it[t.team_pk],
                }
            )

    if not rows:
        print("No backtestable weeks found.")
        return

    df = pd.DataFrame(rows)
    n = len(df)
    coverage = df["in_range"].mean()
    z_mean = df["z"].mean()
    z_std = df["z"].std(ddof=1)

    print(f"\nTeam-weeks analyzed: {n} (weeks 1-{last_completed_week})")
    print(f"\n--- p10/p90 RANGE COVERAGE ---")
    print(f"Target (well-calibrated): 80.0%")
    print(f"Actual: {coverage:.1%}  ({df['in_range'].sum()}/{n} team-weeks landed inside the stated range)")
    print(f"\n--- BIAS / SPREAD DIAGNOSTICS (z = (actual - mean) / stdev) ---")
    print(f"Mean z: {z_mean:+.3f}  (0 = unbiased; positive = real scores running ABOVE projection on average)")
    print(f"Stdev of z: {z_std:.3f}  (1.0 = correctly sized stdev)")
    if z_std > 1.15:
        verdict = "stdev input is TOO SMALL -> ranges are TOO NARROW (real week-to-week variance exceeds what's assumed)"
    elif z_std < 0.85:
        verdict = "stdev input is TOO LARGE -> ranges are TOO WIDE (real week-to-week variance is smaller than assumed)"
    else:
        verdict = "stdev input looks reasonably well-sized"
    print(f"Verdict: {verdict}")

    print(f"\n--- MEDIAN CUTLINE CALIBRATION (p_making_it vs. actually cleared the real weekly median) ---")
    brier = ((df["p_making_it"] - df["actually_made_it"].astype(float)) ** 2).mean()
    print(f"Brier score: {brier:.4f}  (0 = perfect; 0.25 = no better than a flat 50% guess every time)")
    bins = [(0.0, 0.3), (0.3, 0.45), (0.45, 0.55), (0.55, 0.7), (0.7, 1.01)]
    print(f"{'predicted range':<18}{'n':>5}{'avg predicted':>16}{'realized freq':>16}")
    for lo, hi in bins:
        bucket = df[(df["p_making_it"] >= lo) & (df["p_making_it"] < hi)]
        if bucket.empty:
            continue
        print(
            f"{lo:.0%}-{hi:.0%}".ljust(18) + f"{len(bucket):>5}"
            + f"{bucket['p_making_it'].mean():>15.1%} " + f"{bucket['actually_made_it'].mean():>15.1%}"
        )

    print(f"\n--- WORST-CALIBRATED TEAM-WEEKS (biggest |z|, i.e. biggest surprises) ---")
    worst = df.reindex(df["z"].abs().sort_values(ascending=False).index).head(8)
    for _, r in worst.iterrows():
        print(
            f"week {int(r['week']):>2}  {r['team']:<28} mean={r['mean']:>6.1f}  actual={r['actual']:>6.1f}  "
            f"z={r['z']:+.2f}  range=[{r['p10']:.1f}, {r['p90']:.1f}]  in_range={r['in_range']}"
        )

    out_path = Path(tempfile.gettempdir()) / f"backtest_part1_{season}.csv"
    df.to_csv(out_path, index=False)
    print(f"\n(full per-team-week table: {out_path})")


def backtest_playoff_odds_swings(conn, season, last_completed_week, median_scoring, reg_season_count, names):
    print("\n" + "=" * 70)
    print("PART 2: PLAYOFF-ODDS WEEK-TO-WEEK SWINGS")
    print("=" * 70)

    manifest_path = Path("playoff_odds_snapshots") / f"{season}.json"
    if not manifest_path.exists():
        print(f"No snapshot manifest found at {manifest_path} - skipping.")
        return
    manifest = json.loads(manifest_path.read_text())

    weeks_available = sorted((int(w) for w in manifest.keys() if w.isdigit()), key=int)
    print(f"Snapshot weeks available: {weeks_available}")
    if len(weeks_available) < 2:
        print("Need at least 2 snapshot weeks to measure swings - skipping.")
        return

    swing_rows = []
    for prev_w, cur_w in zip(weeks_available, weeks_available[1:]):
        prev_rows = {r["team_pk"]: r["playoff_pct"] for r in manifest[str(prev_w)]}
        cur_rows = {r["team_pk"]: r["playoff_pct"] for r in manifest[str(cur_w)]}
        for pk, cur_pct in cur_rows.items():
            if pk not in prev_rows:
                continue
            swing_rows.append(
                {
                    "from_week": prev_w, "to_week": cur_w, "team_pk": pk, "team": names.get(pk, "?"),
                    "prev_pct": prev_rows[pk], "cur_pct": cur_pct, "delta": cur_pct - prev_rows[pk],
                }
            )
    sw = pd.DataFrame(swing_rows)
    print(f"\nTeam-week transitions measured: {len(sw)}")
    print(f"Mean |swing|: {sw['delta'].abs().mean():.1%}")
    print(f"Median |swing|: {sw['delta'].abs().median():.1%}")
    print(f"Max |swing|: {sw['delta'].abs().max():.1%}")
    print(f"\n--- BIGGEST SWINGS ---")
    biggest = sw.reindex(sw["delta"].abs().sort_values(ascending=False).index).head(8)
    for _, r in biggest.iterrows():
        print(
            f"week {int(r['from_week'])}->{int(r['to_week'])}  {r['team']:<28} "
            f"{r['prev_pct']:.1%} -> {r['cur_pct']:.1%}  ({r['delta']:+.1%})"
        )

    # Decomposition: for each transition where real matchup results exist,
    # isolate how much of the swing is explained by JUST that week's real
    # game outcome (holding the Roster Strength shrinkage prior FIXED at
    # its prev-week value) vs. the rest (prior drift between snapshots -
    # waiver moves, injury news, FantasyPros rank changes, etc.).
    print(f"\n--- SWING DECOMPOSITION: game result vs. everything else ---")
    print("(isolates how much of each real swing is explained by that week's actual games,")
    print(" holding the Roster Strength prior fixed at its prior-week value)\n")

    decomp_rows = []
    for prev_w, cur_w in zip(weeks_available, weeks_available[1:]):
        if prev_w == 0:
            continue  # Week 0 has no real team_state (pre-season) to build a counterfactual from
        team_state = pd.read_sql_query(
            "SELECT team_pk, ppg AS season_ppg, last3_ppg, points_for, matchup_wins, matchup_losses, "
            "matchup_ties, median_wins, median_losses, median_ties FROM metrics_weekly "
            "WHERE season_id = ? AND week = ?", conn, params=(season, prev_w),
        )
        if team_state.empty:
            continue
        scores = pd.read_sql_query(
            "SELECT week, team_pk, score FROM weekly_team_scores WHERE season_id = ? AND week <= ?",
            conn, params=(season, prev_w),
        )
        stdev = compute_score_stdev(scores)
        team_state = team_state.merge(stdev, on="team_pk", how="left")
        if team_state["team_pk"].nunique() < SUPPORTED_PLAYOFF_TEAM_COUNT:
            continue

        remaining_matchups = pd.read_sql_query(
            "SELECT week, home_team_pk, away_team_pk FROM matchups WHERE season_id = ? AND week > ? "
            "AND matchup_type = 'NONE' ORDER BY week",
            conn, params=(season, prev_w),
        )

        # "prev-week prior": the Roster Strength signal as it stood AT prev_w
        # (as-of prev_w+1, matching how the real snapshot for prev_w was built).
        position_slot_counts = json.loads(
            conn.execute("SELECT position_slot_counts FROM seasons WHERE season_id = ?", (season,)).fetchone()[0]
        )
        prior_series = metric_loaders.load_roster_strength_shrinkage_prior(
            conn, season, prev_w + 1, position_slot_counts, reg_season_count
        )
        if prior_series.empty:
            continue
        reindexed_prior = prior_series.reindex(team_state["team_pk"])
        if reindexed_prior.isna().any():
            continue

        actual_week_matchups = pd.read_sql_query(
            "SELECT home_team_pk, away_team_pk, home_score, away_score FROM matchups "
            "WHERE season_id = ? AND week = ? AND matchup_type = 'NONE' AND completed = 1",
            conn, params=(season, cur_w),
        )
        if actual_week_matchups.empty:
            continue

        # Counterfactual A: prev-week state, prev-week prior, cur_w's real
        # result baked in as a FIXED win/loss (everything else simulated
        # randomly) - isolates "what the real game result alone would do."
        flipped_state = team_state.copy()
        for r in actual_week_matchups.itertuples():
            home_won = r.home_score > r.away_score
            winner_pk, loser_pk = (r.home_team_pk, r.away_team_pk) if home_won else (r.away_team_pk, r.home_team_pk)
            w_idx = flipped_state["team_pk"] == winner_pk
            l_idx = flipped_state["team_pk"] == loser_pk
            flipped_state.loc[w_idx, "matchup_wins"] += 1
            flipped_state.loc[l_idx, "matchup_losses"] += 1
        other_matchups = remaining_matchups[remaining_matchups["week"] != cur_w]

        result_only = simulate_season(
            flipped_state, other_matchups, median_scoring, reg_season_count, n_sims=N_SIMS,
            shrinkage_prior=reindexed_prior.to_numpy(),
        ).set_index("team_pk")
        baseline = simulate_season(
            team_state, remaining_matchups, median_scoring, reg_season_count, n_sims=N_SIMS,
            shrinkage_prior=reindexed_prior.to_numpy(),
        ).set_index("team_pk")

        real_rows = manifest.get(str(cur_w), [])
        prev_rows_map = {r["team_pk"]: r["playoff_pct"] for r in manifest.get(str(prev_w), [])}
        for r in real_rows:
            pk = r["team_pk"]
            if pk not in baseline.index or pk not in result_only.index or pk not in prev_rows_map:
                continue
            real_delta = r["playoff_pct"] - prev_rows_map[pk]
            result_only_delta = float(result_only.loc[pk, "playoff_pct"]) - float(baseline.loc[pk, "playoff_pct"])
            decomp_rows.append(
                {
                    "week": f"{prev_w}->{cur_w}", "team": names.get(pk, "?"),
                    "real_delta": real_delta, "result_only_delta": result_only_delta,
                    "other_delta": real_delta - result_only_delta,
                }
            )

    if decomp_rows:
        dd = pd.DataFrame(decomp_rows)
        print(f"Transitions decomposed: {len(dd)}")
        print(f"Mean |real swing|: {dd['real_delta'].abs().mean():.1%}")
        print(f"Mean |explained by that week's game result|: {dd['result_only_delta'].abs().mean():.1%}")
        print(f"Mean |explained by everything else (prior drift)|: {dd['other_delta'].abs().mean():.1%}")
        share_other = dd["other_delta"].abs().sum() / (dd["result_only_delta"].abs().sum() + dd["other_delta"].abs().sum())
        print(f"Share of total swing magnitude from non-game-result drift: {share_other:.1%}")
        print(f"\n--- LARGEST 'OTHER' (non-game-result) SWINGS ---")
        biggest_other = dd.reindex(dd["other_delta"].abs().sort_values(ascending=False).index).head(6)
        for _, r in biggest_other.iterrows():
            print(
                f"week {r['week']:<8} {r['team']:<28} real={r['real_delta']:+.1%}  "
                f"from_result={r['result_only_delta']:+.1%}  from_other={r['other_delta']:+.1%}"
            )
        out_path = Path(tempfile.gettempdir()) / f"backtest_part2_{season}.csv"
        dd.to_csv(out_path, index=False)
        print(f"\n(full decomposition table: {out_path})")
    else:
        print("Could not decompose any transitions (missing team_state/prior data for the relevant weeks).")


def main() -> int:
    client = ESPNClient()
    season = client.credentials.current_season

    with tempfile.TemporaryDirectory() as tmp_dir:
        db.DB_PATH = Path(tmp_dir) / "league.db"
        conn = db.get_connection()
        db.init_db(conn)
        print("Ingesting full season history...")
        ingest_season(conn, client, season, log=lambda *a: None)
        compute_and_store_season_metrics(conn, season)

        meta = conn.execute(
            "SELECT position_slot_counts, median_scoring, reg_season_count FROM seasons WHERE season_id = ?",
            (season,),
        ).fetchone()
        position_slot_counts = json.loads(meta[0])
        median_scoring = bool(meta[1])
        reg_season_count = meta[2]

        row = conn.execute(
            "SELECT MAX(week) FROM weekly_team_scores WHERE season_id = ? AND completed = 1 AND is_playoff = 0",
            (season,),
        ).fetchone()
        last_completed_week = row[0]
        print(f"Season {season}, last completed week: {last_completed_week}\n")

        names_df = pd.read_sql_query(
            "SELECT id AS team_pk, team_name FROM teams WHERE season_id = ?", conn, params=(season,)
        )
        names = dict(zip(names_df["team_pk"], names_df["team_name"]))

        backtest_score_range_and_cutline(conn, season, last_completed_week, position_slot_counts, median_scoring)
        backtest_playoff_odds_swings(conn, season, last_completed_week, median_scoring, reg_season_count, names)

    return 0


if __name__ == "__main__":
    sys.exit(main())
