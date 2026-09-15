"""Per-user access beyond the flat site-wide allowlist
(config.allowed_emails()) - which LEAGUES a signed-in visitor can see,
and whether they get War Room at all.

Two tiers, deliberately kept separate:
- config.admin_email() - the ONE original owner of this deployment
  ("the primary admin"). Implicitly sees every registered league and
  always has War Room - never needs an entry here.
- Everyone else on the site-wide allowlist defaults to seeing ONLY the
  primary league (today's behavior, unchanged) and no War Room, unless
  explicitly granted extra leagues and/or war_room access here.

Durable (git-committed, same GITHUB_TOKEN/pattern as league_registry.py
and friends - no new secret) so grants survive a Streamlit Cloud disk
wipe. Managed via the primary admin's own "Manage league access"
sidebar UI - NOT self-service for anyone else, unlike league_registry.py
(any War Room user can add a league to browse; only the primary admin
decides who gets to see what).

Deliberately does NOT solve per-user ESPN write credentials - a user
granted war_room access can browse the SAME tools the primary admin
can, but "my team" resolution (war_room_data.get_my_team_pk) still
matches against the ONE globally configured SWID, so it only actually
resolves to THAT person's own team if they're the same ESPN account.
Real per-user writes for a genuinely different manager need that
manager's own espn_s2/SWID, a separate, credential-handling feature
not addressed here.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ACCESS_PATH = BASE_DIR / "league_access.json"


def load_access() -> dict[str, dict]:
    """{email: {"leagues": [league_id, ...], "war_room": bool}} for
    every user with a NON-default grant. Empty dict (not an error) if
    nothing's been configured - everyone just gets the default (primary
    league only, no War Room)."""
    if not ACCESS_PATH.exists():
        return {}
    try:
        raw = json.loads(ACCESS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    result = {}
    for email, entry in (raw or {}).items():
        if not isinstance(entry, dict):
            continue
        leagues = entry.get("leagues") or []
        try:
            leagues = [int(lid) for lid in leagues]
        except (TypeError, ValueError):
            leagues = []
        result[email.strip().lower()] = {"leagues": leagues, "war_room": bool(entry.get("war_room"))}
    return result


def save_access(access: dict[str, dict]) -> Path:
    ACCESS_PATH.write_text(json.dumps(access, indent=2))
    return ACCESS_PATH


def _commit_access() -> dict:
    from . import config

    token = config.github_token()
    if not token:
        return {"committed": False, "commit_message": "GITHUB_TOKEN not configured - saved locally only."}

    from . import github_sync

    result = github_sync.commit_file(
        repo=config.github_repo(), token=token, path="league_access.json",
        content_bytes=ACCESS_PATH.read_bytes(), message="Update league access grants",
    )
    return {"committed": result["success"], "commit_message": result["message"]}


def set_user_access(email: str, leagues: list[int], war_room: bool) -> dict:
    access = load_access()
    email = email.strip().lower()
    if not leagues and not war_room:
        access.pop(email, None)
    else:
        access[email] = {"leagues": leagues, "war_room": war_room}
    save_access(access)
    return _commit_access()


def remove_user_access(email: str) -> dict:
    access = load_access()
    access.pop(email.strip().lower(), None)
    save_access(access)
    return _commit_access()


def get_user_leagues(email: str, primary_league_id: int) -> list[int] | str:
    """The league ids this email can see - "all" for the primary admin
    (see module docstring), else [primary_league_id] plus whatever
    extra leagues they've been explicitly granted (deduped, primary
    always included so nobody ever loses access to the original
    league)."""
    from . import config

    email = (email or "").strip().lower()
    if email == (config.admin_email() or ""):
        return "all"
    granted = load_access().get(email, {}).get("leagues", [])
    extras = sorted({lid for lid in granted if lid != primary_league_id})
    return [primary_league_id] + extras


def has_war_room_access(email: str) -> bool:
    from . import config

    email = (email or "").strip().lower()
    if email == (config.admin_email() or ""):
        return True
    return bool(load_access().get(email, {}).get("war_room"))
