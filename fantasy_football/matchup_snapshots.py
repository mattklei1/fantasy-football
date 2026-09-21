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
from .schedule_guard import PACIFIC, is_within_live_window

SNAPSHOT_BRANCH = "matchup-snapshots"

# How stale the last snapshot has to be before a real page load captures
# a fresh one itself (see capture_snapshot_if_due). Shorter than the
# collector's declared 10-minute cron interval on purpose - this is a
# backstop for when that cron doesn't actually fire on time, not a
# competing cadence to tune independently.
MIN_CAPTURE_INTERVAL = datetime.timedelta(minutes=8)

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


def capture_snapshot_if_due(season: int, week: int, existing_snapshots: list[dict]) -> dict | None:
    """Page-load-triggered fallback to the GitHub Actions collector
    (scripts/post_matchup_snapshot.py's "*/10 * * * *" cron). Added
    2026-09-20 after the chart showed almost no movement across two full
    weeks - checked the real snapshot manifests on SNAPSHOT_BRANCH and
    found only 1 point for week 1 and 2 for week 2, all clustered right
    at the START of a live window, none spread through hours of real
    Sunday/Thursday/Monday game time. GitHub's own `schedule:` trigger
    just isn't reliably firing every 10 minutes for this repo (same
    scheduler-reliability issue diagnosed the same session for
    waiver-recap.yml/weekly-recap.yml, see PROJECT_BRIEF) - at that
    frequency, most ticks are apparently dropped rather than delayed.

    Rather than trying to make GitHub's scheduler more reliable (not
    something this repo can control), this applies the same fix already
    proven for Roster Strength staleness (ui_common.
    ensure_daily_data_fresh()): let real traffic on the page that needs
    the data trigger the capture itself. Every real visit to the
    Matchups page during a live window checks whether the last snapshot
    is older than MIN_CAPTURE_INTERVAL and, if so, captures and commits
    one - a safety net alongside the cron collector, not a replacement
    for it (the cron still helps when nobody's visiting). Returns the
    append result dict, or None if nothing was due (outside a live
    window, or the last snapshot is still fresh) - callers should treat
    None as "nothing changed," not an error.

    The WEEK'S VERY FIRST snapshot is a special case, captured
    regardless of is_within_live_window() (user, 2026-09-20: "Shouldn't
    the first data point be pre-Thursday night football games?") - the
    chart's whole point is showing movement FROM a pregame baseline, so
    that baseline can't be allowed to depend on someone happening to
    visit the page in the narrow window between a live window opening
    and actual kickoff. Capturing pregame works fine any time after the
    week's matchups are set (ESPN box_scores() returns real 0-0 pairings
    with real projections well before Thursday) - so the first visitor
    of the week, whenever that happens to be, locks in the true pregame
    read. Every capture AFTER that first one stays live-window-gated, so
    a Tuesday page visit doesn't also start peppering in pointless
    duplicate pregame snapshots all week."""
    if existing_snapshots:
        if not is_within_live_window():
            return None
        last_ts = datetime.datetime.fromisoformat(existing_snapshots[-1]["timestamp"])
        if datetime.datetime.now(datetime.timezone.utc) - last_ts < MIN_CAPTURE_INTERVAL:
            return None
    snapshot = capture_snapshot(season, week)
    if snapshot is None:
        return None
    return append_snapshot(season, week, snapshot)


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


def _dead_time_rangebreaks(snapshots: list[dict]) -> list[dict]:
    """Plotly x-axis rangebreaks compressing out the real dead time
    BETWEEN broadcast days (Thursday night through Sunday morning,
    Sunday night through Monday evening) - the same technique real stock
    charts use to skip weekends/after-hours on an otherwise-continuous
    time axis. Without this, Plotly draws a straight line directly
    connecting the last Thursday-night reading to the first Sunday-
    morning one, which VISUALLY implies a smooth multi-hour drift that
    never actually happened (nothing was live to change) - confirmed by
    rendering it (see PROJECT_BRIEF). Compressing each dead gap down to
    near-zero width on the axis both removes that misleading diagonal
    (it collapses to a near-vertical jump instead) and stops the chart
    wasting most of its width on dead time between game windows.

    BUG fixed 2026-09-21 (user: "The chart lines should all be connected
    even if snapshots every so often"): this used to break on ANY gap
    over a fixed 25-minute threshold, calibrated back when the only
    collector was a reliable-looking 10-minute cron. Once
    capture_snapshot_if_due() started letting real (irregular) page
    visits trigger captures too, a perfectly normal gap between two
    visits DURING THE SAME live window (an hour between Sunday
    check-ins, say) routinely exceeded 25 minutes and got wrongly
    treated as dead time - fragmenting each team's line into disconnected
    islands of isolated dots instead of one connected line per window,
    exactly what the screenshot showed. A fixed minute threshold can't
    tell "still the same live window, just sparsely sampled" apart from
    "genuinely between windows" once sampling isn't on a reliable clock.
    Grouping by actual PACIFIC CALENDAR DATE instead does: only the gap
    BETWEEN two different days' snapshots is dead time, so every gap
    within a single day's own data stays connected no matter how large,
    while Thu->Sun and Sun->Mon jumps still compress correctly. Safe
    because no LIVE_GAME_WINDOWS_BY_WEEKDAY window crosses midnight
    Pacific."""
    timestamps = sorted({s["timestamp"] for s in snapshots if s.get("timestamp")})
    if len(timestamps) < 2:
        return []
    parsed = [datetime.datetime.fromisoformat(t) for t in timestamps]
    return [
        {"bounds": [prev.isoformat(), cur.isoformat()]}
        for prev, cur in zip(parsed, parsed[1:])
        if prev.astimezone(PACIFIC).date() != cur.astimezone(PACIFIC).date()
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
