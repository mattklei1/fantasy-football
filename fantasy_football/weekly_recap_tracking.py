"""Tracks which week's Weekly Recap has already been posted to GroupMe,
committed to `main` as a small JSON marker (same GitHub Contents API
pattern as playoff_odds_snapshots.py) so it survives across GitHub
Actions runs - each run gets a throwaway temp DB with no memory of
previous runs (see scripts/post_weekly_recap.py's module docstring).

Exists because GitHub Actions' own cron scheduler turned out to be
unreliable for this repo (confirmed via real run history, 2026-10-16):
a workflow declared to fire at a precise time, or even just daily, can
actually fire hours late and skip days outright - the platform throttles
this account's total scheduled executions well below what's declared.
Fighting that with ever-narrower time windows doesn't work; the fix is
to stop depending on WHEN the job runs at all. post_weekly_recap.py now
fires on a loose daily-ish schedule and uses THIS marker, not the clock,
to decide whether there's a new week to post - correct no matter how
late or irregularly GitHub actually invokes it, and safe to invoke
multiple times for the same week (idempotent)."""
from __future__ import annotations

import json

from . import config

_PATH = "weekly_recap_posts/last_posted.json"


def load_last_posted_week(season: int) -> int | None:
    """The last week's recap successfully posted to GroupMe for this
    season, or None if nothing's been posted yet (including when
    GITHUB_TOKEN isn't configured - degrades to "nothing posted yet"
    rather than erroring, matching playoff_odds_snapshots.load_snapshots()'s
    same tradeoff)."""
    token = config.github_token()
    if not token:
        return None
    from . import github_sync

    repo = config.github_repo()
    existing = github_sync.get_file_content(repo, token, _PATH, "main")
    if existing is None:
        return None
    content_bytes, _sha = existing
    try:
        manifest = json.loads(content_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    return manifest.get(str(season))


def save_last_posted_week(season: int, week: int) -> dict:
    """Records that `week` has been posted for `season` and commits to
    main. Returns {"success", "message"} - a missing GITHUB_TOKEN
    degrades to a clear no-op message rather than an error."""
    token = config.github_token()
    if not token:
        return {"success": False, "message": "GITHUB_TOKEN not configured - marker not saved."}
    from . import github_sync

    repo = config.github_repo()
    existing = github_sync.get_file_content(repo, token, _PATH, "main")
    manifest: dict[str, int] = {}
    if existing is not None:
        try:
            manifest = json.loads(existing[0].decode("utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    manifest[str(season)] = week

    content_bytes = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8")
    result = github_sync.commit_file(
        repo=repo, token=token, path=_PATH, content_bytes=content_bytes,
        message=f"Weekly recap posted: season {season} week {week}", branch="main",
    )
    return {"success": result["success"], "message": result["message"]}
