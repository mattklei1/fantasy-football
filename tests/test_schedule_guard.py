"""Unit tests for fantasy_football.schedule_guard - DST-safety logic."""
import datetime
from zoneinfo import ZoneInfo

from fantasy_football import schedule_guard
from fantasy_football.schedule_guard import PACIFIC, is_target_time_now, should_refresh_daily


def _freeze_now(monkeypatch, dt: datetime.datetime):
    class _FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.astimezone(tz) if tz else dt

    # should_refresh_daily also uses datetime.timedelta/datetime.timezone
    # from this same module-level `datetime` reference, so the frozen
    # stand-in must expose those too, not just `.datetime`.
    monkeypatch.setattr(
        schedule_guard,
        "datetime",
        type("M", (), {"datetime": _FrozenDatetime, "timedelta": datetime.timedelta, "timezone": datetime.timezone}),
    )


def test_within_tolerance_matches(monkeypatch):
    _freeze_now(monkeypatch, datetime.datetime(2026, 11, 15, 13, 35, tzinfo=PACIFIC))
    assert is_target_time_now(13, 30, tolerance_minutes=12) is True


def test_outside_tolerance_does_not_match(monkeypatch):
    _freeze_now(monkeypatch, datetime.datetime(2026, 11, 15, 14, 5, tzinfo=PACIFIC))
    assert is_target_time_now(13, 30, tolerance_minutes=12) is False


def test_exact_target_matches(monkeypatch):
    _freeze_now(monkeypatch, datetime.datetime(2026, 11, 15, 13, 30, tzinfo=PACIFIC))
    assert is_target_time_now(13, 30, tolerance_minutes=12) is True


def test_dst_transition_still_resolves_correct_pacific_time(monkeypatch):
    # Nov 1 2026 is still PDT (UTC-7); Nov 15 2026 is PST (UTC-8) - both
    # must independently match the SAME Pacific wall-clock target,
    # proving the check is DST-safe rather than a fixed UTC offset.
    _freeze_now(monkeypatch, datetime.datetime(2026, 11, 1, 13, 30, tzinfo=PACIFIC))
    assert is_target_time_now(13, 30, tolerance_minutes=12) is True

    _freeze_now(monkeypatch, datetime.datetime(2026, 11, 15, 13, 30, tzinfo=PACIFIC))
    assert is_target_time_now(13, 30, tolerance_minutes=12) is True


def test_should_refresh_daily_never_refreshed():
    assert should_refresh_daily(None) is True


def test_should_refresh_daily_false_right_after_todays_boundary(monkeypatch):
    # 7am Pacific - just past today's 6am boundary. A refresh recorded
    # 30 min ago (6:30am) is still fresh.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 15, 7, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 6, 30, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_daily(last_refreshed) is False


def test_should_refresh_daily_true_once_next_boundary_passes(monkeypatch):
    # Same last-refresh timestamp as above, but now it's the FOLLOWING
    # morning - a new daily boundary has passed since then.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 16, 7, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 6, 30, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_daily(last_refreshed) is True


def test_should_refresh_daily_false_later_same_day_after_morning_refresh(monkeypatch):
    # 9pm the same day as a 6:30am refresh - no new boundary has passed
    # yet, should stay frozen until tomorrow's 6am.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 15, 21, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 6, 30, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_daily(last_refreshed) is False


def test_should_refresh_daily_true_early_morning_before_yesterdays_refresh_boundary(monkeypatch):
    # 3am Pacific - before TODAY's 6am boundary, so "today's boundary" is
    # actually yesterday 6am. A refresh from yesterday at 5am (before
    # yesterday's own boundary) is stale relative to it.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 16, 3, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 5, 0, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_daily(last_refreshed) is True
