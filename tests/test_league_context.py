"""Unit tests for fantasy_football.league_context - id resolution and
DB path routing, against a fake st.session_state dict (no real
Streamlit runtime) and a monkeypatched primary-credentials lookup, no
network/DB."""
from fantasy_football import config, league_context


class _FakeSessionState(dict):
    """dict already satisfies session_state's .get()/pop()/[]= surface
    for these tests - just a readable alias."""


def _use_fake_session(monkeypatch):
    fake = _FakeSessionState()
    monkeypatch.setattr(league_context.st, "session_state", fake)
    return fake


def _use_primary(monkeypatch, league_id=1025842):
    creds = config.ESPNCredentials(league_id=league_id, espn_s2="s2", swid="{SWID}", current_season=2026)
    monkeypatch.setattr(league_context.config, "load_espn_credentials", lambda: creds)
    return creds


def test_get_active_league_id_defaults_to_primary(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    assert league_context.get_active_league_id() == 1025842


def test_set_active_league_id_then_get_returns_it(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    assert league_context.get_active_league_id() == 999999


def test_set_active_league_id_to_primary_sticks_explicitly(monkeypatch):
    """An explicit switch back to the primary league must stick for the
    rest of the session, not silently pop back to the session key and
    let a granted user's own default (see _default_league_id) snap them
    right back to their extra league on the next rerun."""
    session = _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    league_context.set_active_league_id(1025842)
    assert league_context.get_active_league_id() == 1025842
    assert session[league_context.SESSION_KEY] == 1025842


def test_set_active_league_id_none_resets_to_primary(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    league_context.set_active_league_id(None)
    assert league_context.get_active_league_id() == 1025842


def _use_signed_in(monkeypatch, email):
    monkeypatch.setattr(league_context, "_current_user_email", lambda: email)


def test_default_league_id_is_primary_for_ungranted_user(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    _use_signed_in(monkeypatch, "randomvisitor@example.com")
    monkeypatch.setattr(league_context.config, "admin_email", lambda: "admin@example.com")
    monkeypatch.setattr(
        "fantasy_football.access_control.get_user_leagues", lambda email, primary_id: [primary_id]
    )
    assert league_context.get_active_league_id() == 1025842


def test_default_league_id_is_the_users_own_granted_extra_league(monkeypatch):
    """user, 2026-09-16: "have it default to whatever league they are
    given access to" - a granted user should land on THEIR league, not
    the primary one, every session, with no manual re-pick needed."""
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    _use_signed_in(monkeypatch, "grantcohen7@example.com")
    monkeypatch.setattr(league_context.config, "admin_email", lambda: "admin@example.com")
    monkeypatch.setattr(
        "fantasy_football.access_control.get_user_leagues", lambda email, primary_id: [primary_id, 1243473]
    )
    assert league_context.get_active_league_id() == 1243473


def test_default_league_id_is_still_primary_for_the_admin(monkeypatch):
    """The admin implicitly sees every league (access_control.get_user_
    leagues returns "all" for them) - they still default to their OWN
    primary/home league, not an arbitrary extra one."""
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    _use_signed_in(monkeypatch, "admin@example.com")
    monkeypatch.setattr(league_context.config, "admin_email", lambda: "admin@example.com")
    monkeypatch.setattr(
        "fantasy_football.access_control.get_user_leagues", lambda email, primary_id: "all"
    )
    assert league_context.get_active_league_id() == 1025842


def test_explicit_session_choice_overrides_the_granted_default(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    _use_signed_in(monkeypatch, "grantcohen7@example.com")
    monkeypatch.setattr(league_context.config, "admin_email", lambda: "admin@example.com")
    monkeypatch.setattr(
        "fantasy_football.access_control.get_user_leagues", lambda email, primary_id: [primary_id, 1243473]
    )
    league_context.set_active_league_id(1025842)
    assert league_context.get_active_league_id() == 1025842


def test_get_active_league_id_tolerates_no_streamlit_session(monkeypatch):
    # simulates a script/test context with no real session_state at all
    class _Explode:
        def get(self, *a, **k):
            raise RuntimeError("no session")

    monkeypatch.setattr(league_context.st, "session_state", _Explode())
    _use_primary(monkeypatch, league_id=1025842)
    assert league_context.get_active_league_id() == 1025842


def test_get_active_db_path_is_primary_path_for_primary_league(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    assert league_context.get_active_db_path() == config.DB_PATH


def test_get_active_db_path_is_per_league_file_for_extra_league(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    path = league_context.get_active_db_path()
    assert path != config.DB_PATH
    assert path.name == "league_999999.db"
    assert path.parent == config.DB_PATH.parent


def test_get_active_espn_client_uses_primary_credentials_for_primary_league(monkeypatch):
    _use_fake_session(monkeypatch)
    creds = _use_primary(monkeypatch, league_id=1025842)
    client = league_context.get_active_espn_client()
    assert client.credentials == creds


def test_get_active_espn_client_swaps_league_id_but_keeps_same_account_creds(monkeypatch):
    _use_fake_session(monkeypatch)
    creds = _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    client = league_context.get_active_espn_client()
    assert client.credentials.league_id == 999999
    assert client.credentials.espn_s2 == creds.espn_s2
    assert client.credentials.swid == creds.swid
    assert client.credentials.current_season == creds.current_season


def test_get_active_espn_client_uses_signed_in_managers_own_credentials(monkeypatch):
    """A different real manager (e.g. Madeline) with her own ESPN
    session configured must get HER OWN espn_s2/SWID, not the shared
    primary account's - this is what makes "my team"/real writes
    resolve to her team specifically."""
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    monkeypatch.setattr(league_context, "_current_user_email", lambda: "madeline@example.com")
    monkeypatch.setattr(
        league_context.config, "get_manager_credentials",
        lambda email: ("madeline-s2", "{MADELINE-SWID}") if email == "madeline@example.com" else None,
    )
    league_context.set_active_league_id(1827422962)

    client = league_context.get_active_espn_client()

    assert client.credentials.league_id == 1827422962
    assert client.credentials.espn_s2 == "madeline-s2"
    assert client.credentials.swid == "{MADELINE-SWID}"


def test_get_active_espn_client_falls_back_to_primary_when_user_has_no_own_credentials(monkeypatch):
    _use_fake_session(monkeypatch)
    creds = _use_primary(monkeypatch, league_id=1025842)
    monkeypatch.setattr(league_context, "_current_user_email", lambda: "randomvisitor@example.com")
    monkeypatch.setattr(league_context.config, "get_manager_credentials", lambda email: None)

    client = league_context.get_active_espn_client()

    assert client.credentials == creds
