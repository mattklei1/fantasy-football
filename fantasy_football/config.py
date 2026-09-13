"""Environment-based configuration. All credentials come from environment
variables (loaded from a local .env file via python-dotenv) - never hardcoded.
"""
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "data" / "league.db"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


@dataclass(frozen=True)
class ESPNCredentials:
    league_id: int
    espn_s2: str
    swid: str
    current_season: int


def _current_calendar_season() -> int:
    """ESPN's NFL season year rolls over in March, well after the Super Bowl."""
    today = datetime.date.today()
    return today.year if today.month >= 3 else today.year - 1


def load_espn_credentials() -> ESPNCredentials:
    league_id_raw = os.getenv("LEAGUE_ID", "").strip()
    espn_s2 = os.getenv("ESPN_S2", "").strip()
    swid = os.getenv("SWID", "").strip()
    season_raw = os.getenv("CURRENT_SEASON", "").strip()

    missing = [
        name
        for name, value in (("LEAGUE_ID", league_id_raw), ("ESPN_S2", espn_s2), ("SWID", swid))
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in your ESPN credentials."
        )

    try:
        league_id = int(league_id_raw)
    except ValueError as exc:
        raise ConfigError(f"LEAGUE_ID must be numeric, got: {league_id_raw!r}") from exc

    season = int(season_raw) if season_raw else _current_calendar_season()

    return ESPNCredentials(league_id=league_id, espn_s2=espn_s2, swid=swid, current_season=season)


def anthropic_api_key() -> str | None:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    return key or None


def fantasypros_api_key() -> str | None:
    key = os.getenv("FANTASYPROS_API_KEY", "").strip()
    return key or None


def gemini_api_key() -> str | None:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return key or None


def app_password() -> str | None:
    """Optional shared password gate (see ui_common.require_password()) -
    for a hosted deployment reachable by more than just the developer.
    Unset locally on purpose: local dev shouldn't need a password prompt
    on every run."""
    pw = os.getenv("APP_PASSWORD", "").strip()
    return pw or None


def gemini_model() -> str:
    """Configurable rather than hardcoded - Gemini model names change
    fast (verified 'gemini-2.5-flash' is real and current as of
    2026-09-13, cross-checked against the google-genai SDK's own PyPI/
    GitHub examples, but by the time you're reading this a newer Flash
    model may be preferred - check https://ai.google.dev/gemini-api/docs/models
    and set GEMINI_MODEL in .env rather than editing code)."""
    return os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
