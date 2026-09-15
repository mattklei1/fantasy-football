"""DST-safe "should I actually run right now" check for the GitHub
Actions cron jobs (scripts/post_*.py). GitHub Actions cron runs in UTC
with no DST awareness, but the NFL season (Sept-Feb) crosses the
November US DST transition, so a single fixed UTC cron time would drift
an hour off the intended Pacific time for part of the season.

Fix: schedule the GitHub Actions workflow to fire several times across a
window that covers the target Pacific time under BOTH PDT and PST (see
.github/workflows/*.yml), and have the script itself decide whether to
actually do anything using real timezone-aware Pacific time via
zoneinfo (which handles DST correctly automatically) - firings outside
the tolerance window no-op immediately, before any ESPN/GroupMe call, so
they cost a few seconds of Actions runtime and nothing else."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")


def is_target_time_now(target_hour: int, target_minute: int = 0, tolerance_minutes: int = 12) -> bool:
    """True if the current Pacific time is within `tolerance_minutes` of
    hour:minute Pacific, today. Tolerance should be >= half the gap
    between the workflow's scheduled firings, so at least one firing
    always lands inside the window."""
    now = datetime.datetime.now(PACIFIC)
    target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    delta = abs((now - target).total_seconds()) / 60
    return delta <= tolerance_minutes


#: Real NFL game windows, in Pacific local time (Thu/Mon night games kick
#: off ~5:15pm PT, Sunday's early slate ~10am PT, running through Sunday
#: Night Football ending ~8:45pm PT). Generous on both ends on purpose -
#: this only gates the matchup-snapshot collector (scripts/post_matchup_
#: snapshot.py), where an extra few wasted ticks pregame/postgame cost
#: nothing (one skipped ESPN call), but missing a real live window would
#: leave a visible gap in the win-probability chart.
LIVE_GAME_WINDOWS_BY_WEEKDAY = {
    3: (16, 30, 21, 0),   # Thursday: 4:30pm-9:00pm PT (TNF)
    6: (9, 30, 21, 0),    # Sunday: 9:30am-9:00pm PT (early slate through SNF)
    0: (16, 30, 21, 0),   # Monday: 4:30pm-9:00pm PT (MNF)
}


def is_within_live_window(now: datetime.datetime | None = None) -> bool:
    """True during the real NFL broadcast windows this league's games
    are actually played in (see LIVE_GAME_WINDOWS_BY_WEEKDAY) - used to
    gate the matchup-snapshot collector so it only actually calls ESPN
    (and commits a snapshot) while scores can realistically be changing,
    not around the clock every 10 minutes for a full week."""
    now = now or datetime.datetime.now(PACIFIC)
    window = LIVE_GAME_WINDOWS_BY_WEEKDAY.get(now.weekday())
    if window is None:
        return False
    start_hour, start_minute, end_hour, end_minute = window
    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    return start <= now <= end


def should_refresh_daily(last_refreshed_at: str | None, hour: int = 6) -> bool:
    """True if a daily boundary has passed since `last_refreshed_at` (a
    UTC timestamp string as SQLite's datetime('now') produces, or None if
    never refreshed). Default boundary is 6am Pacific.

    Originally this was a WEEKLY gate timed to land after FantasyPros'
    Tuesday ~5pm ET Rest-of-Season rankings snapshot - but their own
    Accuracy FAQ (re-verified 2026-09-14) says that Tuesday deadline is
    only an accuracy-GRADING snapshot for scoring individual experts;
    the actual served ROS consensus updates continuously all week as
    experts revise. There's no meaningful weekly boundary to wait for,
    so this is now a plain daily gate instead - keeps Roster Strength
    reasonably current without redoing the FantasyPros/ESPN rank pull on
    every single page load or "Refresh ESPN Data" click."""
    now = datetime.datetime.now(PACIFIC)
    boundary = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if boundary > now:
        boundary -= datetime.timedelta(days=1)

    if last_refreshed_at is None:
        return True
    last = datetime.datetime.fromisoformat(last_refreshed_at)
    if last.tzinfo is None:
        last = last.replace(tzinfo=datetime.timezone.utc)
    return last < boundary
