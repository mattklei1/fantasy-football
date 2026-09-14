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
    on every run. Legacy: superseded by real per-user Google login (see
    admin_email()/allowed_emails() below) but left in place as a backup
    gate during the transition - harmless if both are configured, since
    require_login() still requires the visitor's own email be on the
    allowlist regardless of whether the shared password was also entered."""
    pw = os.getenv("APP_PASSWORD", "").strip()
    return pw or None


def admin_email() -> str | None:
    """The one Google account (see ui_common.require_login()/is_admin())
    that can see the commissioner-only War Room page - everyone else who
    signs in sees it locked. Not the same as being on ALLOWED_EMAILS:
    this address should also be listed there (or logging in won't get
    past the gate at all), this just additionally unlocks the extra
    page."""
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    return email or None


def allowed_emails() -> set[str]:
    """Comma-separated allowlist of Google account emails permitted to use
    the deployed site at all (see ui_common.require_login()) - a private,
    real-money friend league, so a real Google sign-in alone isn't
    enough, it also has to be an email the commissioner actually put on
    this list. Empty by default (fails CLOSED: nobody but ADMIN_EMAIL
    gets in until this is set), not "allow everyone" - a wide-open
    empty-allowlist default would be an easy way to accidentally expose
    real-money league data to the entire internet."""
    raw = os.getenv("ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def groupme_bot_id() -> str | None:
    """The GroupMe Bot's `bot_id` (from dev.groupme.com, after creating a
    bot for the league's group) - required to post scheduled messages
    (see groupme_client.py). Not the personal access token used earlier
    this project for read-only message history research - a Bot's
    bot_id can only post, it has no read/account access at all."""
    bot_id = os.getenv("GROUPME_BOT_ID", "").strip()
    return bot_id or None


def groupme_personal_bot_id() -> str | None:
    """A SEPARATE GroupMe bot, bound to a private group containing only
    the commissioner - used for the Tuesday waiver-recommendations
    notification (and anything else that shouldn't go to the whole
    league's shared bot/group). A GroupMe bot can only post to the one
    group it was created for (confirmed - see groupme_client.py's
    module docstring), so this can't just reuse groupme_bot_id()."""
    bot_id = os.getenv("GROUPME_PERSONAL_BOT_ID", "").strip()
    return bot_id or None


def gemini_model() -> str:
    """Configurable rather than hardcoded - Gemini model names change
    fast (verified 'gemini-2.5-flash' is real and current as of
    2026-09-13, cross-checked against the google-genai SDK's own PyPI/
    GitHub examples, but by the time you're reading this a newer Flash
    model may be preferred - check https://ai.google.dev/gemini-api/docs/models
    and set GEMINI_MODEL in .env rather than editing code)."""
    return os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
