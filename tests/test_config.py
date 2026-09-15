"""Unit tests for fantasy_football.config.get_manager_credentials - no
real Streamlit secrets, monkeypatched st.secrets."""
import streamlit as st

from fantasy_football import config


def test_returns_none_when_no_manager_credentials_table(monkeypatch):
    monkeypatch.setattr(st, "secrets", {})
    assert config.get_manager_credentials("someone@example.com") is None


def test_returns_none_when_email_has_no_entry(monkeypatch):
    monkeypatch.setattr(st, "secrets", {"manager_credentials": {"other@example.com": {"espn_s2": "x", "swid": "y"}}})
    assert config.get_manager_credentials("someone@example.com") is None


def test_returns_credentials_tuple_when_configured(monkeypatch):
    monkeypatch.setattr(
        st, "secrets",
        {"manager_credentials": {"madeline@example.com": {"espn_s2": "s2val", "swid": "{SWID-VAL}"}}},
    )
    assert config.get_manager_credentials("madeline@example.com") == ("s2val", "{SWID-VAL}")


def test_email_lookup_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(
        st, "secrets",
        {"manager_credentials": {"madeline@example.com": {"espn_s2": "s2val", "swid": "{SWID-VAL}"}}},
    )
    assert config.get_manager_credentials("Madeline@Example.com") == ("s2val", "{SWID-VAL}")


def test_returns_none_when_entry_missing_swid(monkeypatch):
    monkeypatch.setattr(st, "secrets", {"manager_credentials": {"madeline@example.com": {"espn_s2": "s2val"}}})
    assert config.get_manager_credentials("madeline@example.com") is None


def test_returns_none_when_secrets_unavailable(monkeypatch):
    class _NoSecrets:
        def get(self, *a, **k):
            raise Exception("no secrets.toml")

    monkeypatch.setattr(st, "secrets", _NoSecrets())
    assert config.get_manager_credentials("madeline@example.com") is None
