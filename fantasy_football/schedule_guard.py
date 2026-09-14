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
