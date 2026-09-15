"""Weekly playoff-odds-over-time tracking (fantasy_football.metrics.
playoff_sim's Monte Carlo simulation results), for the Playoff Odds
page's trend chart - one line per team, toggle between championship% /
playoff% / bye% / #1-seed%, x-axis = week number across the season.

Collected by scripts/post_playoff_odds_snapshot.py, a GitHub Actions job
firing once a day. Self-gates BEFORE any expensive work (see that
script's own docstring) so most daily firings cost one lightweight ESPN
call and nothing else - the actual write only happens once per real
week, whenever that day's run lands after the week rolls over.

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

from . import config

METRICS = ("championship_pct", "playoff_pct", "bye_pct", "seed1_pct")


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


def build_chart_figure(manifest: dict[str, list[dict]], metric_key: str):
    """The actual go.Figure for the Playoff Odds Over Time chart - one
    line per team, x=week number, y=whichever metric_key the page's
    toggle selected. Pulled out of pages/6_Playoff_Odds.py so it's
    directly callable (and visually checkable) without going through
    Streamlit - same convention as matchup_snapshots.build_chart_figure."""
    import plotly.graph_objects as go

    from .matchup_snapshots import _line_colors

    rows = snapshots_to_rows(manifest)
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

    fig.update_xaxes(title="Week", dtick=1, tick0=1)
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
