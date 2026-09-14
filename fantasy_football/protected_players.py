"""Storage for admin-marked "do not drop" players (see
war_room_data.get_protected_player_ids()/save_protected_players() and
the My Waiver Bids tab in pages/9_War_Room.py) - waiver-suggestion drop
candidates never include a player on this list.

Keyed by ESPN's own espn_team_id (stable across a fresh DB rebuild -
unlike the DB's own autoincrement team_pk, which a from-scratch ingest
in a different environment, e.g. a GitHub Actions run's throwaway temp
DB, has no guarantee of reproducing identically).

Lives OUTSIDE data/ on purpose - same reasoning as rank_overrides.py:
data/ is entirely gitignored and gets rebuilt from live ESPN calls every
fresh session/Streamlit Cloud wake, so anything meant to persist across
that (a manager's standing "never suggest dropping this guy" list is
exactly that, not a one-week thing) has to live in real tracked repo
content instead.
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROTECTED_DIR = BASE_DIR / "protected_players"


def protected_path(espn_team_id: int) -> Path:
    return PROTECTED_DIR / f"{espn_team_id}.json"


def load_protected(espn_team_id: int) -> set[int]:
    """Player ids this team's manager has marked "do not drop" - empty
    set (not an error) if nothing's been saved for this team yet."""
    import json

    path = protected_path(espn_team_id)
    if not path.exists():
        return set()
    try:
        return {int(pid) for pid in json.loads(path.read_text()).get("player_ids", [])}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return set()


def save_protected(espn_team_id: int, player_ids: set[int]) -> Path:
    import json

    PROTECTED_DIR.mkdir(parents=True, exist_ok=True)
    path = protected_path(espn_team_id)
    path.write_text(json.dumps({"espn_team_id": espn_team_id, "player_ids": sorted(player_ids)}, indent=2))
    return path
