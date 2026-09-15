"""Durable storage for the admin's OTHER ESPN leagues (same account,
different league_id) - the "select at the top what league this is for"
feature, admin-only per the original request. Same account means
ESPN_S2/SWID stay shared (see config.py); only league_id differs, so
this registry is just {league_id: {"name", "season"}} - the credentials
themselves are never duplicated.

Committed to git via github_sync.py (same GITHUB_TOKEN already set up
for rank overrides/protected players - no new secret needed) so the
registered leagues list survives a Streamlit Cloud disk wipe: losing it
wouldn't lose any LEAGUE data (each league's own SQLite file is
separately gitignored/rebuildable from ESPN, same as the primary
league's data/league.db always has been), but it WOULD force the admin
to re-add every extra league by hand after every redeploy, which is
exactly the kind of standing preference this project's established
pattern (rank_overrides.py, protected_players.py) already durably
persists.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = BASE_DIR / "leagues.json"


def load_registered_leagues() -> dict[int, dict]:
    """{league_id: {"name": str, "season": int}} for every EXTRA league
    the admin has added - NOT including the primary env-configured
    league (that one's always implicitly available, see
    league_context.py). Empty dict (not an error) if none registered."""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        raw = json.loads(REGISTRY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    result = {}
    for k, v in (raw or {}).items():
        try:
            result[int(k)] = {"name": v.get("name"), "season": v.get("season")}
        except (TypeError, ValueError, AttributeError):
            continue
    return result


def save_registered_leagues(leagues: dict[int, dict]) -> Path:
    REGISTRY_PATH.write_text(json.dumps({str(k): v for k, v in leagues.items()}, indent=2))
    return REGISTRY_PATH


def _commit_registry() -> dict:
    """Writes the just-saved registry to git too, same durability
    pattern as rank_overrides.py/protected_players.py - degrades to a
    clear "local only" message rather than an error when GITHUB_TOKEN
    isn't configured."""
    from . import config

    token = config.github_token()
    if not token:
        return {"committed": False, "commit_message": "GITHUB_TOKEN not configured - saved locally only."}

    from . import github_sync

    result = github_sync.commit_file(
        repo=config.github_repo(), token=token, path="leagues.json",
        content_bytes=REGISTRY_PATH.read_bytes(), message="Update registered leagues",
    )
    return {"committed": result["success"], "commit_message": result["message"]}


def add_league(league_id: int, name: str, season: int) -> dict:
    leagues = load_registered_leagues()
    leagues[league_id] = {"name": name, "season": season}
    save_registered_leagues(leagues)
    return _commit_registry()


def remove_league(league_id: int) -> dict:
    leagues = load_registered_leagues()
    leagues.pop(league_id, None)
    save_registered_leagues(leagues)
    return _commit_registry()
