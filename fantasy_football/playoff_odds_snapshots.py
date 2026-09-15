"""Weekly playoff-odds-over-time tracking (fantasy_football.metrics.
playoff_sim's Monte Carlo simulation results), for the Playoff Odds
page's trend chart - one line per team, toggle between championship% /
playoff% / bye% / #1-seed%, x-axis = week number across the season.

Collected by scripts/post_playoff_odds_snapshot.py, a GitHub Actions job
firing once a day. Self-gates BEFORE any expensive work (see that
script's own docstring) so most daily firings cost one lightweight ESPN
call and nothing else - the actual write only happens once per real
week, whenever that day's run lands after the week rolls over.

Two points precede real Week-1 results (user feedback 2026-09-15 -
"show the first data point as pre-draft... then show week 0... after
draft but before week 1 games"):
- **Pre-draft** (x=-1): every team is equally likely - a pure fair-share
  constant, synthesized on demand, never stored (preseason_baseline_
  rows()).
- **Week 0** (x=0): REAL playoff odds computed from Week-1 roster
  quality alone, before any games were played - each team's expected
  score is their optimal Week-1 lineup's total using ESPN's real
  PREGAME Week-1 projections (confirmed these stay frozen at their
  pregame values, not overwritten once games start or finish - e.g. a
  real check against this league's actual week-1 data: a player
  projected 18.49 who then scored 0.0 still shows 18.49, not something
  adjusted toward the real result). This is stored in the same manifest
  as every other week (key "0"), computed once and never recomputed -
  see compute_week0_snapshot_rows().

STORAGE, and why it's the normal "commit to main" pattern (unlike
matchup_snapshots.py's dedicated non-deploying branch): that one exists
because a 10-minute cadence committing to `main` would redeploy the live
site every 10 minutes for ~16 hours a week. This is genuinely low
frequency - at most once a week - so one extra redeploy a week costs
nothing, and reuses the same simple pattern already used for
rank_overrides.py/league_registry.py.

One growing JSON file per season (not per week) - {week_str: [{team_pk,
team_name, championship_pct, playoff_pct, bye_pct, seed1_pct}, ...]}."""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from . import config

METRICS = ("championship_pct", "playoff_pct", "bye_pct", "seed1_pct")

#: The only bracket shape playoff_sim.py supports (top-2-bye, 6-team
#: field) - see that module's own SUPPORTED_PLAYOFF_TEAM_COUNT.
PLAYOFF_BYE_COUNT = 2


def preseason_baseline_rows(team_names: dict[int, str], playoff_team_count: int = 6) -> list[dict]:
    """Before the draft, every team is equally likely - fair-share odds
    for N teams / playoff_team_count playoff spots / 2 byes / 1 eventual
    champion. Used as the "Pre-draft" FIRST point on the Playoff Odds
    Over Time chart and as a fallback "season long" baseline for the
    weekly recap's PLAYOFF ODDS section riser callout, when a real
    Week-0 snapshot (see compute_week0_snapshot_rows()) isn't available
    yet. Not stored anywhere - a pure constant derived from league size
    and format, not real collected data, so it's always synthesized on
    demand rather than persisted."""
    n = len(team_names)
    if n == 0:
        return []
    return [
        {
            "team_pk": pk, "team_name": name,
            "playoff_pct": playoff_team_count / n,
            "bye_pct": PLAYOFF_BYE_COUNT / n,
            "seed1_pct": 1 / n,
            "championship_pct": 1 / n,
        }
        for pk, name in team_names.items()
    ]


def compute_week0_team_state(conn: sqlite3.Connection, season: int, position_slot_counts: dict) -> pd.DataFrame:
    """team_state (same shape playoff_sim.simulate_season expects) for
    "right after the draft, before any Week-1 games" - matchup/median
    records and points_for all zero (nothing's been decided yet),
    season_ppg/last3_ppg set to each team's OPTIMAL Week-1 lineup total
    using ESPN's real PREGAME Week-1 projections (the same eligible-
    slots-respecting optimal_lineup() solver used for the Weekly
    Recap's NEXT WEEK'S GAME TO WATCH - see commentary.
    _team_next_week_optimal_projection), score_stdev set to win_
    probability.MIN_STDEV (this league's own empirically-grounded
    small-sample floor, ~20 points - there's no real per-team variance
    yet to compute one from). Empty DataFrame if Week-1 roster/
    projection data isn't available."""
    from .metrics.lineup_optimizer import RosterPlayer, optimal_lineup
    from .metrics.win_probability import MIN_STDEV

    rows = pd.read_sql_query(
        """
        SELECT wr.team_pk, wr.player_id, wr.eligible_slots, pws.projected_points
        FROM weekly_rosters wr
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = 1 AND wr.eligible_slots IS NOT NULL
        """,
        conn, params=(season,),
    )
    if rows.empty:
        return pd.DataFrame()
    rows["eligible_slots"] = rows["eligible_slots"].apply(lambda s: frozenset(json.loads(s)))
    rows["projected_points"] = rows["projected_points"].fillna(0.0)

    team_rows = []
    for team_pk, g in rows.groupby("team_pk"):
        players = [
            RosterPlayer(int(r.player_id), float(r.projected_points), r.eligible_slots)
            for r in g.itertuples()
        ]
        optimal_points, _ = optimal_lineup(players, position_slot_counts)
        team_rows.append(
            {
                "team_pk": int(team_pk), "season_ppg": optimal_points, "last3_ppg": optimal_points,
                "score_stdev": MIN_STDEV, "matchup_wins": 0, "matchup_losses": 0, "matchup_ties": 0,
                "median_wins": 0, "median_losses": 0, "median_ties": 0, "points_for": 0.0,
            }
        )
    return pd.DataFrame(team_rows)


def compute_week0_snapshot_rows(conn: sqlite3.Connection, season: int, n_sims: int = 10_000) -> list[dict] | None:
    """Real playoff odds computed from Week-1 roster/projection quality
    alone - right after the draft, before any games (see
    compute_week0_team_state()). Simulates the FULL regular-season
    schedule as "remaining" (every real matchup, regardless of its
    current completed status - from this vantage point nothing has
    happened yet). None if this season's format isn't the 6-team/top-2-
    bye shape playoff_sim.py supports, or there's no Week-1 roster data
    to compute from yet (before the draft, or too early in a brand new
    season for rosters to be set)."""
    from .metrics.playoff_sim import SUPPORTED_PLAYOFF_TEAM_COUNT, simulate_season

    meta = conn.execute(
        "SELECT playoff_team_count, median_scoring, reg_season_count, position_slot_counts "
        "FROM seasons WHERE season_id = ?",
        (season,),
    ).fetchone()
    if not meta or meta[0] != SUPPORTED_PLAYOFF_TEAM_COUNT or not meta[3]:
        return None
    _, median_scoring, reg_season_count, position_slot_counts_json = meta
    position_slot_counts = json.loads(position_slot_counts_json)

    team_state = compute_week0_team_state(conn, season, position_slot_counts)
    if team_state.empty or team_state["team_pk"].nunique() < SUPPORTED_PLAYOFF_TEAM_COUNT:
        return None

    remaining_matchups = pd.read_sql_query(
        "SELECT week, home_team_pk, away_team_pk FROM matchups WHERE season_id = ? AND matchup_type = 'NONE' "
        "ORDER BY week",
        conn, params=(season,),
    )

    result = simulate_season(
        team_state, remaining_matchups, bool(median_scoring), reg_season_count, n_sims=n_sims,
    )
    name_rows = conn.execute("SELECT id, team_name FROM teams WHERE season_id = ?", (season,)).fetchall()
    name_by_pk = {r[0]: r[1] for r in name_rows}
    return [
        {
            "team_pk": int(row.team_pk), "team_name": name_by_pk.get(int(row.team_pk), "Unknown"),
            "championship_pct": float(row.championship_pct), "playoff_pct": float(row.playoff_pct),
            "bye_pct": float(row.bye_pct), "seed1_pct": float(row.seed1_pct),
        }
        for row in result.itertuples()
    ]


def _path(season: int) -> str:
    return f"playoff_odds_snapshots/{season}.json"


def load_snapshots(season: int) -> dict[str, list[dict]]:
    """{week_str: [team rows]} for every week captured so far this
    season. Empty dict (not an error) if no GITHUB_TOKEN is configured
    or nothing's been committed yet."""
    token = config.github_token()
    if not token:
        return {}
    from . import github_sync

    repo = config.github_repo()
    existing = github_sync.get_file_content(repo, token, _path(season), "main")
    if existing is None:
        return {}
    content_bytes, _sha = existing
    try:
        return json.loads(content_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def save_snapshot(season: int, week: int, rows: list[dict]) -> dict:
    """Adds/overwrites this week's rows in the season's manifest and
    commits to main. rows: [{"team_pk", "team_name", "championship_pct",
    "playoff_pct", "bye_pct", "seed1_pct"}, ...]. Returns
    {"success", "message"} - a missing GITHUB_TOKEN degrades to a clear
    no-op message rather than an error, matching every other optional
    GitHub-durability feature in this project."""
    token = config.github_token()
    if not token:
        return {"success": False, "message": "GITHUB_TOKEN not configured - snapshot not saved."}
    from . import github_sync

    repo = config.github_repo()
    path = _path(season)
    existing = github_sync.get_file_content(repo, token, path, "main")
    manifest: dict[str, list[dict]] = {}
    if existing is not None:
        try:
            manifest = json.loads(existing[0].decode("utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    manifest[str(week)] = rows

    content_bytes = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    result = github_sync.commit_file(
        repo=repo, token=token, path=path, content_bytes=content_bytes,
        message=f"Playoff odds snapshot: season {season} week {week}", branch="main",
    )
    return {"success": result["success"], "message": result["message"]}


def snapshots_to_rows(manifest: dict[str, list[dict]]) -> list[dict]:
    """Long-format rows (one per week x team) - what the page hands to
    the Plotly line chart, one line per team_name, x=week (int),
    y=whichever metric the toggle selects."""
    rows = []
    for week_str, team_rows in manifest.items():
        try:
            week = int(week_str)
        except ValueError:
            continue
        for team in team_rows:
            rows.append({"week": week, **team})
    return rows


def build_chart_figure(
    manifest: dict[str, list[dict]], metric_key: str,
    team_names: dict[int, str] | None = None, playoff_team_count: int = 6,
):
    """The actual go.Figure for the Playoff Odds Over Time chart - one
    line per team, x=week number (week -1 = a synthetic "Pre-draft"
    point, fair-share odds before the draft, see preseason_baseline_
    rows(); week 0 = the REAL "Week 0" point, computed from actual
    Week-1 roster/projection quality, see compute_week0_snapshot_rows()
    - present here only if `manifest` already has a "0" entry, i.e. it's
    been collected), y=whichever metric_key the page's toggle selected.
    Pulled out of pages/6_Playoff_Odds.py so it's directly callable (and
    visually checkable) without going through Streamlit - same
    convention as matchup_snapshots.build_chart_figure.

    Only ONE point per real week is ever possible here by construction -
    save_snapshot()/the collector script key each week's row by week
    number and overwrite rather than append (unlike matchup_snapshots.py's
    growing intra-week JSONL manifest), so there is no mid-week noise to
    filter out (user feedback 2026-09-15: "I don't want to see midweek
    changes"). `team_names` (only needed to synthesize the Pre-draft
    point - omit to just chart the real collected weeks starting at
    week 0/1) should be the CURRENT full team roster for the season, not
    just teams that have snapshot history yet."""
    import plotly.graph_objects as go

    from .matchup_snapshots import _line_colors

    full_manifest = dict(manifest)
    if team_names:
        full_manifest.setdefault("-1", preseason_baseline_rows(team_names, playoff_team_count))

    rows = snapshots_to_rows(full_manifest)
    by_team: dict[str, list[dict]] = {}
    for r in rows:
        by_team.setdefault(r["team_name"], []).append(r)

    colors = _line_colors()
    fig = go.Figure()
    for i, (team_name, team_rows) in enumerate(sorted(by_team.items())):
        team_rows = sorted(team_rows, key=lambda r: r["week"])
        fig.add_trace(
            go.Scatter(
                x=[r["week"] for r in team_rows],
                y=[r.get(metric_key) for r in team_rows],
                mode="lines+markers",
                name=team_name,
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=6),
            )
        )

    all_weeks = sorted({r["week"] for r in rows})
    tick_labels = {-1: "Pre-draft", 0: "Week 0"}
    fig.update_xaxes(
        title=None, dtick=1, tick0=-1, tickmode="array", tickvals=all_weeks,
        ticktext=[tick_labels.get(w, f"Post Wk{w}") for w in all_weeks],
    )
    fig.update_yaxes(tickformat=".0%", range=[0, 1])
    fig.update_layout(
        height=480,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
        margin=dict(t=10, l=10, r=10, b=10),
    )
    return fig


def biggest_mover(manifest: dict[str, list[dict]], metric: str = "championship_pct") -> dict | None:
    """Whoever's `metric` swung the most between the last two captured
    weeks - the chart's "biggest mover" callout. None if fewer than 2
    weeks are captured yet (nothing to compare)."""
    weeks = sorted((int(w) for w in manifest.keys()), reverse=True)
    if len(weeks) < 2:
        return None
    prev_rows = {r["team_pk"]: r for r in manifest[str(weeks[1])]}
    latest_rows = {r["team_pk"]: r for r in manifest[str(weeks[0])]}

    best = None
    for team_pk, latest in latest_rows.items():
        prev = prev_rows.get(team_pk)
        if prev is None or latest.get(metric) is None or prev.get(metric) is None:
            continue
        delta = latest[metric] - prev[metric]
        if best is None or abs(delta) > abs(best["delta"]):
            best = {"team_name": latest.get("team_name"), "delta": delta, "value": latest[metric]}
    return best
