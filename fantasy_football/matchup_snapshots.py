"""Live win-probability/projected-score/median-cutline tracking over the
course of a real NFL week, for the Matchups page's new chart (a line per
team, toggle between win% / projected score / chance to make the median
cutline, x-axis spanning Thursday kickoff through Monday Night Football).

Collected by scripts/post_matchup_snapshot.py, a GitHub Actions cron
firing every 10 minutes but self-gating on schedule_guard.
is_within_live_window() - real scores only change during actual game
windows, so sampling around the clock for a full week would just repeat
the same static pregame number for free.

STORAGE, and why it's NOT the usual "commit to main" pattern this
project uses elsewhere (rank_overrides.py, protected_players.py):
committing every 10 minutes to `main` would make Streamlit Cloud
redeploy the live site every 10 minutes for the ~16 live hours/week
this collects data - dropping every visitor's session on each restart,
for a feature that's purely additive chart data. Instead this commits
to a SEPARATE branch (SNAPSHOT_BRANCH) that Streamlit Cloud never
watches for deploys - the live app reads that branch's content directly
over the GitHub API (get_file_content(), same github_sync.py helper the
write side uses) instead of relying on its own local checkout, so new
data shows up on the next page load/cache refresh with zero redeploys.

One growing JSONL manifest file per (season, week) - not one file per
snapshot - so the chart only ever needs ONE file fetch per page load
(cached) instead of listing+fetching ~100 small files. Each collector
run does one read-modify-write (fetch current content+sha, append one
line, PUT with that sha) - a real but small and accepted race window,
since only a single 10-minute cron writes here.
"""
from __future__ import annotations

import datetime
import json

from . import config
from .schedule_guard import PACIFIC

SNAPSHOT_BRANCH = "matchup-snapshots"

# (weekday [Mon=0..Sun=6], hour, minute) in Pacific time - the chart's
# x-axis tick labels. Approximate real NFL broadcast-window END times;
# precision to the minute doesn't matter, these are visual anchors, not
# a schedule lookup - see benchmark_ticks().
BENCHMARK_MARKERS = [
    (3, 20, 45, "Post-TNF"),
    (6, 13, 15, "Post-10am games"),
    (6, 16, 45, "Post-1pm games"),
    (6, 20, 30, "Post-SNF"),
    (0, 20, 30, "Post-MNF"),
]

METRICS = ("win_probability", "projected_score", "p_making_it")


def _manifest_path(season: int, week: int) -> str:
    return f"matchup_snapshots/{season}_wk{week}.jsonl"


def capture_snapshot(season: int, week: int) -> dict | None:
    """Live ESPN call: this week's win_probability/projected_score/
    p_making_it for every team currently in a real matchup (a bye
    doesn't have one). Returns None (not an error) if there's nothing to
    capture yet (off-season, or the week just hasn't started) - callers
    should skip committing in that case rather than write an empty
    snapshot."""
    from . import dashboard_data as dd
    from .espn_client import ESPNClient

    league = ESPNClient().get_league(season)
    box_scores = league.box_scores(week)
    conn = dd.get_connection()

    projected_by_team: dict[int, float] = {}
    score_by_team: dict[int, float] = {}
    name_by_team: dict[int, str] = {}
    pairs: list[tuple[int, int]] = []
    for bs in box_scores:
        if bs.home_team is None or bs.away_team is None:
            continue
        row_h = conn.execute(
            "SELECT id FROM teams WHERE season_id = ? AND espn_team_id = ?", (season, bs.home_team.team_id)
        ).fetchone()
        row_a = conn.execute(
            "SELECT id FROM teams WHERE season_id = ? AND espn_team_id = ?", (season, bs.away_team.team_id)
        ).fetchone()
        if not row_h or not row_a:
            continue
        h_pk, a_pk = row_h[0], row_a[0]
        projected_by_team[h_pk] = bs.home_projected or 0.0
        projected_by_team[a_pk] = bs.away_projected or 0.0
        score_by_team[h_pk] = bs.home_score or 0.0
        score_by_team[a_pk] = bs.away_score or 0.0
        name_by_team[h_pk] = getattr(bs.home_team, "team_name", str(bs.home_team))
        name_by_team[a_pk] = getattr(bs.away_team, "team_name", str(bs.away_team))
        pairs.append((h_pk, a_pk))

    if not pairs:
        return None

    cutline = dd.live_cutline_analysis(season, projected_by_team, score_by_team)

    teams: dict[str, dict] = {}
    for h_pk, a_pk in pairs:
        win_prob_h = dd.live_win_probability(
            season, h_pk, a_pk, projected_by_team[h_pk], projected_by_team[a_pk],
            score_by_team[h_pk], score_by_team[a_pk],
        )
        for pk, win_prob in ((h_pk, win_prob_h), (a_pk, 1.0 - win_prob_h)):
            teams[str(pk)] = {
                "team_name": name_by_team[pk],
                "win_probability": win_prob,
                "projected_score": projected_by_team[pk],
                "p_making_it": (cutline.get(pk) or {}).get("p_making_it"),
            }

    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "season": season,
        "week": week,
        "teams": teams,
    }


def append_snapshot(season: int, week: int, snapshot: dict) -> dict:
    """Appends one JSON line to this week's growing snapshot manifest on
    SNAPSHOT_BRANCH (creating the branch/file on first use) and commits
    it. Returns {"success", "message"}; a missing GITHUB_TOKEN degrades
    to a clear no-op message rather than an error - matching every other
    optional-GitHub-durability feature in this project (see
    config.github_token()'s docstring)."""
    token = config.github_token()
    if not token:
        return {"success": False, "message": "GITHUB_TOKEN not configured - snapshot not saved."}
    repo = config.github_repo()

    from . import github_sync

    branch_result = github_sync.ensure_branch_exists(repo, token, SNAPSHOT_BRANCH, base_branch="main")
    if not branch_result["success"]:
        return {"success": False, "message": f"Couldn't create/find {SNAPSHOT_BRANCH}: {branch_result['message']}"}

    path = _manifest_path(season, week)
    existing = github_sync.get_file_content(repo, token, path, SNAPSHOT_BRANCH)
    new_line = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode("utf-8")
    content_bytes = (existing[0] + new_line) if existing is not None else new_line

    result = github_sync.commit_file(
        repo=repo, token=token, path=path, content_bytes=content_bytes,
        message=f"Matchup snapshot {snapshot['timestamp']}", branch=SNAPSHOT_BRANCH,
    )
    return {"success": result["success"], "message": result["message"]}


def load_snapshots(season: int, week: int) -> list[dict]:
    """This week's full time series (every snapshot committed so far),
    oldest first - what the live app's chart renders. Empty list (not an
    error) if no GITHUB_TOKEN is configured or nothing's been collected
    yet for this week."""
    token = config.github_token()
    if not token:
        return []
    from . import github_sync

    repo = config.github_repo()
    existing = github_sync.get_file_content(repo, token, _manifest_path(season, week), SNAPSHOT_BRANCH)
    if existing is None:
        return []
    content_bytes, _sha = existing

    snapshots = []
    for line in content_bytes.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            snapshots.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a rare partially-written line from a mid-write race - skip, don't crash the chart
    return snapshots


def snapshots_to_rows(snapshots: list[dict]) -> list[dict]:
    """Long-format rows (one per timestamp x team): {"timestamp",
    "team_pk", "team_name", "win_probability", "projected_score",
    "p_making_it"} - what pages/1_Matchups.py hands to the Plotly line
    chart, one line per team_name, x=timestamp, y=whichever metric the
    toggle selects."""
    rows = []
    for snap in snapshots:
        ts = snap.get("timestamp")
        for team_pk, team in (snap.get("teams") or {}).items():
            rows.append(
                {
                    "timestamp": ts,
                    "team_pk": team_pk,
                    "team_name": team.get("team_name"),
                    "win_probability": team.get("win_probability"),
                    "projected_score": team.get("projected_score"),
                    "p_making_it": team.get("p_making_it"),
                }
            )
    return rows


def biggest_mover(snapshots: list[dict], metric: str = "win_probability") -> dict | None:
    """Whoever's `metric` swung the most between the last two snapshots -
    the chart's "biggest mover" callout. None if fewer than 2 snapshots
    exist yet (nothing to compare)."""
    if len(snapshots) < 2:
        return None
    prev_teams = snapshots[-2].get("teams") or {}
    latest_teams = snapshots[-1].get("teams") or {}

    best = None
    for team_pk, latest in latest_teams.items():
        prev = prev_teams.get(team_pk)
        if prev is None or latest.get(metric) is None or prev.get(metric) is None:
            continue
        delta = latest[metric] - prev[metric]
        if best is None or abs(delta) > abs(best["delta"]):
            best = {"team_name": latest.get("team_name"), "delta": delta, "value": latest[metric]}
    return best


#: A 12-team league needs 12 visually distinct line colors - Plotly's
#: DEFAULT qualitative palette only has 10, so team #11/#12 would
#: silently repeat a color already used by someone else, making two
#: different teams' lines indistinguishable. Light24 has 24 distinct
#: colors, comfortably covering any league size this project supports.
#: Populated lazily (see _line_colors()) so importing this module
#: doesn't require plotly for callers that only need the pure functions
#: above (e.g. the unit tests).
_LINE_COLORS = None


def _line_colors():
    global _LINE_COLORS
    if _LINE_COLORS is None:
        import plotly.colors as pc

        _LINE_COLORS = pc.qualitative.Light24
    return _LINE_COLORS


#: Comfortably above the normal ~10-minute collection cadence, safely
#: below the multi-hour dead time between real game windows (Thursday
#: night through Sunday morning, Sunday night through Monday evening) -
#: see _dead_time_rangebreaks().
DEAD_TIME_GAP_THRESHOLD_MINUTES = 25


def _dead_time_rangebreaks(snapshots: list[dict]) -> list[dict]:
    """Plotly x-axis rangebreaks compressing out any real gap between
    consecutive snapshots wider than DEAD_TIME_GAP_THRESHOLD_MINUTES -
    the same technique real stock charts use to skip weekends/after-
    hours on an otherwise-continuous time axis. Without this, Plotly
    draws a straight line directly connecting the last Thursday-night
    reading to the first Sunday-morning one, which VISUALLY implies a
    smooth multi-hour drift that never actually happened (nothing was
    live to change) - confirmed by rendering it (see PROJECT_BRIEF).
    Compressing each dead gap down to near-zero width on the axis both
    removes that misleading diagonal (it collapses to a near-vertical
    jump instead) and stops the chart wasting most of its width on dead
    time between game windows - directly answers "set the zoom to a
    good spot" without hardcoding what a "good spot" is: it's derived
    from the actual gaps in the collected data, not assumed NFL times."""
    timestamps = sorted({s["timestamp"] for s in snapshots if s.get("timestamp")})
    if len(timestamps) < 2:
        return []
    parsed = [datetime.datetime.fromisoformat(t) for t in timestamps]
    threshold = datetime.timedelta(minutes=DEAD_TIME_GAP_THRESHOLD_MINUTES)
    return [
        {"bounds": [prev.isoformat(), cur.isoformat()]}
        for prev, cur in zip(parsed, parsed[1:])
        if cur - prev > threshold
    ]


def build_chart_figure(snapshots: list[dict], metric_key: str):
    """The actual go.Figure for the Win Probability Over Time chart -
    one line per team, x=real timestamp, y=whichever metric_key the
    page's toggle selected ("win_probability"/"projected_score"/
    "p_making_it"). Pulled out of pages/1_Matchups.py so it's directly
    callable (and visually checkable) without going through Streamlit -
    used by both the live page and ad-hoc visual QA."""
    import plotly.graph_objects as go

    rows = snapshots_to_rows(snapshots)
    by_team: dict[str, list[dict]] = {}
    for r in rows:
        by_team.setdefault(r["team_name"], []).append(r)

    colors = _line_colors()
    fig = go.Figure()
    for i, (team_name, team_rows) in enumerate(sorted(by_team.items())):
        team_rows = sorted(team_rows, key=lambda r: r["timestamp"])
        fig.add_trace(
            go.Scatter(
                x=[r["timestamp"] for r in team_rows],
                y=[r[metric_key] for r in team_rows],
                mode="lines+markers",
                name=team_name,
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=4),
            )
        )

    ticks = benchmark_ticks(snapshots)
    if ticks:
        fig.update_xaxes(tickmode="array", tickvals=[t for t, _ in ticks], ticktext=[lbl for _, lbl in ticks])
    rangebreaks = _dead_time_rangebreaks(snapshots)
    if rangebreaks:
        fig.update_xaxes(rangebreaks=rangebreaks)

    if metric_key in ("win_probability", "p_making_it"):
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
    else:
        all_values = [r[metric_key] for r in rows if r.get(metric_key) is not None]
        if all_values:
            pad = max(2.0, (max(all_values) - min(all_values)) * 0.08)
            fig.update_yaxes(title="Projected points", range=[min(all_values) - pad, max(all_values) + pad])
        else:
            fig.update_yaxes(title="Projected points")

    fig.update_layout(
        height=480,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
        margin=dict(t=10, l=10, r=10, b=10),
    )
    return fig


def benchmark_ticks(snapshots: list[dict]) -> list[tuple[str, str]]:
    """(iso_timestamp, label) for each real NFL-week broadcast-window
    benchmark (see BENCHMARK_MARKERS) that falls within the collected
    snapshots' date range - the chart's x-axis tick labels. Anchored to
    the ACTUAL calendar dates the snapshots span (not hardcoded season
    dates), so this works for any week without a real NFL schedule
    lookup. Empty list if there's no data yet."""
    if not snapshots:
        return []
    timestamps = [datetime.datetime.fromisoformat(s["timestamp"]) for s in snapshots if s.get("timestamp")]
    if not timestamps:
        return []
    start_date = min(timestamps).astimezone(PACIFIC).date()
    end_date = max(timestamps).astimezone(PACIFIC).date()

    ticks = []
    for weekday, hour, minute, label in BENCHMARK_MARKERS:
        d = start_date
        while d <= end_date:
            if d.weekday() == weekday:
                dt = datetime.datetime(d.year, d.month, d.day, hour, minute, tzinfo=PACIFIC)
                ticks.append((dt.isoformat(), label))
                break
            d += datetime.timedelta(days=1)
    return ticks
