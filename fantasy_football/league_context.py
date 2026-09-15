"""Resolves WHICH league the current Streamlit session is looking at -
the primary env-configured league for every visitor, or an ADMIN's own
chosen alternate league (session-scoped only - never affects any other
visitor's view, and never persists across a page reload on its own,
only the registered-leagues LIST itself is durable, see
league_registry.py).

Everything downstream of this module already works per-league for free
once two things are routed through here instead of being called bare:
- db.DB_PATH (sync_db_path()) - every dashboard_data.py/war_room_data.py
  function already goes through db.get_connection(), which reads
  db.DB_PATH fresh at call time (the exact mechanism the throwaway-
  temp-DB scheduled scripts already exploit - see PROJECT_BRIEF) - so
  setting it once per page load, before any data call, is enough.
- ESPNClient() (get_active_espn_client()) - ESPNClient already accepts
  an optional `credentials` override (see espn_client.py), so this
  just builds the right ESPNCredentials for whichever league_id is
  active instead of always defaulting to the primary one.

Each extra league gets its OWN SQLite file (data/league_{id}.db) -
deliberately NOT a shared multi-tenant schema (the seasons table's
season_id is a bare year, not composite with league_id, so two
leagues' 2026 seasons would collide in one shared file) - this needed
zero schema changes, just routing which file gets opened.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from . import config

SESSION_KEY = "active_league_id"


def _primary_credentials() -> config.ESPNCredentials:
    return config.load_espn_credentials()


def _current_user_email() -> str | None:
    try:
        if not st.user.is_logged_in:
            return None
    except Exception:  # noqa: BLE001 - no Streamlit auth/session context (e.g. a script)
        return None
    return (st.user.email or "").strip().lower() or None


def _default_league_id() -> int:
    """Which league a visitor lands on when they haven't explicitly
    picked one THIS session - the primary league for the admin and for
    anyone with no extra grants (unchanged, original behavior), but
    their own first GRANTED extra league for anyone access_control.py
    has explicitly given one to (user, 2026-09-16: "have it default to
    whatever league they are given access to" - before this, a
    single-extra-league user like an overlap manager landed on the
    PRIMARY league every time and had to manually re-pick their own
    league every single session, since league selection was purely
    st.session_state - gone the moment the browser session ended).
    Recomputed from the durable, git-committed league_access.json on
    every call rather than a separately stored "last picked league" -
    it's already durable and free to read, and it means a NEWLY granted
    league takes effect immediately without a separate migration."""
    primary_id = _primary_credentials().league_id
    email = _current_user_email()
    if not email or email == (config.admin_email() or ""):
        return primary_id

    from . import access_control

    granted = access_control.get_user_leagues(email, primary_id)
    if isinstance(granted, list):
        extras = [lid for lid in granted if lid != primary_id]
        if extras:
            return extras[0]
    return primary_id


def get_active_league_id() -> int:
    """The currently selected league's ESPN id. Session-scoped once
    someone has explicitly picked one THIS session (see
    set_active_league_id) - before that, or in a brand new session,
    falls back to _default_league_id() (see its docstring), not always
    the bare primary league."""
    try:
        selected = st.session_state.get(SESSION_KEY)
    except Exception:  # noqa: BLE001 - no active Streamlit session (e.g. a script/test context)
        selected = None
    return selected if selected is not None else _default_league_id()


def set_active_league_id(league_id: int | None) -> None:
    """Records an EXPLICIT choice for this session, primary league
    included - a granted user who manually switches back to the
    primary league needs that to stick for the rest of the session, not
    silently snap back to their own default league on the next rerun
    (which naively popping the session key on `== primary_id`, the old
    behavior, would cause now that the fallback isn't always primary
    - see _default_league_id). None clears any explicit choice,
    reverting to the default."""
    if league_id is None:
        st.session_state.pop(SESSION_KEY, None)
    else:
        st.session_state[SESSION_KEY] = league_id


def get_active_espn_client():
    """ESPNClient for whichever league is currently active.

    If the SIGNED-IN user has their own ESPN session credentials
    configured (config.get_manager_credentials() - a different real
    manager, e.g. Madeline, using HER OWN espn_s2/SWID rather than the
    shared primary account's), those are used instead - this is what
    makes "my team" resolution (war_room_data.get_my_team_pk) and real
    writes (waiver claims, lineup submits) correctly resolve to HER
    team specifically, not the primary account's, since every one of
    those already keys off client.credentials.swid. Falls back to the
    primary env-configured credentials (same ESPN_S2/SWID, league_id
    swapped for a selected extra league) for everyone else - unchanged
    from before per-manager credentials existed."""
    from .espn_client import ESPNClient

    primary = _primary_credentials()
    league_id = get_active_league_id()

    email = _current_user_email()
    manager_creds = config.get_manager_credentials(email) if email else None
    if manager_creds:
        espn_s2, swid = manager_creds
        return ESPNClient(
            credentials=config.ESPNCredentials(
                league_id=league_id, espn_s2=espn_s2, swid=swid, current_season=primary.current_season,
            )
        )

    if league_id == primary.league_id:
        return ESPNClient(credentials=primary)
    return ESPNClient(
        credentials=config.ESPNCredentials(
            league_id=league_id, espn_s2=primary.espn_s2, swid=primary.swid, current_season=primary.current_season,
        )
    )


def get_active_db_path() -> Path:
    """Where this league's own SQLite file lives - the primary league
    keeps using the original data/league.db (no migration needed for
    existing deployments), every extra league gets data/league_{id}.db."""
    primary = _primary_credentials()
    league_id = get_active_league_id()
    if league_id == primary.league_id:
        return config.DB_PATH
    return config.DB_PATH.parent / f"league_{league_id}.db"


def sync_db_path() -> None:
    """Points db.DB_PATH at the currently active league's own file -
    call this once per page load, before any db.get_connection() call
    (see ui_common.render_sidebar(), which every page calls first)."""
    from . import db

    db.DB_PATH = get_active_db_path()
