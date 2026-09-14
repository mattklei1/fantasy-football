"""Storage for admin-uploaded weekly rank overrides (see
rank_override_pdf.py for parsing, war_room_data.save_rank_override()/
build_ideal_lineup() for how these supersede FantasyPros' weekly
consensus). Keyed by (season, week) only - deliberately NOT by league -
so the same override applies "across all my leagues" per the original
request, regardless of which league's roster build_ideal_lineup() is
evaluating at the time; matching happens by normalized player NAME at
lookup time (player_matching.normalize_name), not a pre-resolved ESPN
player_id, so it works for any league's roster without needing a
separate ID-resolution step per league.

Lives OUTSIDE data/ on purpose - data/ is entirely gitignored (rebuilt
from live ESPN calls on every fresh session, see README/PROJECT_BRIEF),
but an override needs to durably survive a Streamlit Cloud disk wipe and
reach the completely separate GitHub Actions environment the Wednesday/
Sunday scripts run in - the only way to do that in this project is to
actually commit the file to the repo (see github_sync.py), so this
directory is real, tracked, version-controlled content, not ephemeral
cache.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from . import player_matching

BASE_DIR = Path(__file__).resolve().parent.parent
OVERRIDES_DIR = BASE_DIR / "rank_overrides"


def override_path(season: int, week: int) -> Path:
    return OVERRIDES_DIR / f"{season}_wk{week}.json"


def save_override(season: int, week: int, ranks: list[dict], source_filename: str | None) -> Path:
    """Writes the override file locally (immediate effect for the CURRENT
    running app session/process - see war_room_data.save_rank_override()
    for why this alone isn't durable and needs a git commit on top).
    ranks: list of {"rank": int, "player_name": str}, already reviewed/
    edited by the admin - this function trusts it as-is, no parsing here."""
    OVERRIDES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "season": season,
        "week": week,
        "source_filename": source_filename,
        "uploaded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ranks": [{"rank": int(r["rank"]), "player_name": str(r["player_name"])} for r in ranks],
    }
    path = override_path(season, week)
    path.write_text(json.dumps(payload, indent=2))
    return path


def load_override_meta(season: int, week: int) -> dict | None:
    """Raw stored payload (season/week/source_filename/uploaded_at/ranks)
    or None if no override has been uploaded for this week - used by the
    UI to show "current override" status."""
    path = override_path(season, week)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_override(season: int, week: int) -> dict[str, int] | None:
    """{normalized_player_name: rank} for build_ideal_lineup() to look up
    against each rostered player's own name (normalize_name() applied
    identically on both sides at lookup time - see module docstring for
    why matching happens here rather than against a pre-resolved ESPN
    id). None if no override is active for this week."""
    meta = load_override_meta(season, week)
    if meta is None:
        return None
    return {
        player_matching.normalize_name(r["player_name"]): r["rank"]
        for r in meta.get("ranks", [])
        if r.get("player_name") is not None and r.get("rank") is not None
    }


def clear_override(season: int, week: int) -> bool:
    """Deletes the local override file. Returns True if a file actually
    existed and was removed. Callers wanting this to also stay cleared in
    git/for the scheduled scripts must also commit the deletion - see
    war_room_data.clear_rank_override()."""
    path = override_path(season, week)
    if not path.exists():
        return False
    path.unlink()
    return True
