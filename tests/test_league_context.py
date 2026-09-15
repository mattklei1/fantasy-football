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


def test_set_active_league_id_to_primary_clears_session_override(monkeypatch):
    session = _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    league_context.set_active_league_id(1025842)
    assert league_context.get_active_league_id() == 1025842
    assert league_context.SESSION_KEY not in session


def test_set_active_league_id_none_resets_to_primary(monkeypatch):
    _use_fake_session(monkeypatch)
    _use_primary(monkeypatch, league_id=1025842)
    league_context.set_active_league_id(999999)
    league_context.set_active_league_id(None)
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
