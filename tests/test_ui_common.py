"""Unit tests for the deployment-hardening helpers in ui_common.py -
require_password() and ensure_data_bootstrapped(). Full widget-click
interaction (typing a password, clicking Enter) isn't meaningfully
testable outside a real `streamlit run` session - that behavior was
validated directly in a real browser instead (gate blocks direct page
navigation, wrong password rejected, correct password grants access).
These tests cover the pure branching logic: skip vs. act."""
import sqlite3

import pytest
import streamlit as st

from fantasy_football import config, ui_common


@pytest.fixture(autouse=True)
def clear_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


def test_require_password_noop_when_unset(monkeypatch):
    monkeypatch.setattr(config, "app_password", lambda: None)
    # must return normally (not call st.stop(), which would abort the
    # test process) when no password is configured
    ui_common.require_password()


def test_require_password_noop_when_already_authenticated(monkeypatch):
    monkeypatch.setattr(config, "app_password", lambda: "secret123")
    st.session_state["authenticated"] = True
    ui_common.require_password()


def test_require_login_noop_when_auth_not_configured():
    # no secrets.toml in the test environment at all -> must return
    # normally (not call st.stop()), same as require_password() above
    ui_common.require_login()


def test_is_admin_false_when_not_logged_in():
    # st.user outside a real script run reports not logged in
    assert ui_common.is_admin() is False


def test_is_admin_false_when_admin_email_unset(monkeypatch):
    monkeypatch.setattr(config, "admin_email", lambda: None)
    assert ui_common.is_admin() is False


def test_allowed_emails_parses_and_lowercases(monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "Alice@Example.com, bob@example.com ,")
    assert config.allowed_emails() == {"alice@example.com", "bob@example.com"}


def test_allowed_emails_empty_by_default(monkeypatch):
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    assert config.allowed_emails() == set()


def test_admin_email_lowercased(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Mattklei1@Gmail.com")
    assert config.admin_email() == "mattklei1@gmail.com"


def test_ensure_data_bootstrapped_skips_refresh_when_data_exists(monkeypatch, tmp_path):
    db_path = tmp_path / "league.db"
    conn = sqlite3.connect(db_path)
    from fantasy_football import db as db_module

    db_module.init_db(conn)
    conn.execute(
        "INSERT INTO seasons (season_id, league_id, reg_season_count, playoff_team_count) "
        "VALUES (2025, 1, 14, 6)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(ui_common.dd, "get_connection", lambda: sqlite3.connect(db_path))

    called = {"refresh": False}

    def fake_refresh_all(*args, **kwargs):
        called["refresh"] = True

    monkeypatch.setattr("fantasy_football.ingest.refresh_all", fake_refresh_all)

    ui_common.ensure_data_bootstrapped()
    assert called["refresh"] is False


def test_ensure_data_bootstrapped_triggers_refresh_when_empty(monkeypatch, tmp_path):
    db_path = tmp_path / "league.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(ui_common.dd, "get_connection", lambda: sqlite3.connect(db_path))
    monkeypatch.setattr(ui_common.dd, "clear_all_caches", lambda: None)

    called = {"refresh": False, "metrics": False}

    def fake_refresh_all(*args, **kwargs):
        called["refresh"] = True

    def fake_compute_and_store_all_seasons(*args, **kwargs):
        called["metrics"] = True

    monkeypatch.setattr("fantasy_football.ingest.refresh_all", fake_refresh_all)
    monkeypatch.setattr(
        "fantasy_football.metrics.pipeline.compute_and_store_all_seasons", fake_compute_and_store_all_seasons
    )
    monkeypatch.setattr("fantasy_football.espn_client.ESPNClient", lambda: object())

    ui_common.ensure_data_bootstrapped()
    assert called == {"refresh": True, "metrics": True}
