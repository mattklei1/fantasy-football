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
