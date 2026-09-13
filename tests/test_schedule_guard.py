"""Unit tests for fantasy_football.schedule_guard - DST-safety logic."""
import datetime
from zoneinfo import ZoneInfo

from fantasy_football import schedule_guard
from fantasy_football.schedule_guard import PACIFIC, is_target_time_now, should_refresh_weekly


def _freeze_now(monkeypatch, dt: datetime.datetime):
    class _FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.astimezone(tz) if tz else dt

    # should_refresh_weekly also uses datetime.timedelta/datetime.timezone
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


def test_should_refresh_weekly_never_refreshed():
    assert should_refresh_weekly(None) is True


def test_should_refresh_weekly_false_right_after_this_weeks_boundary(monkeypatch):
    # Tuesday 2026-09-15, 7pm Pacific - just past this week's 6pm Tuesday
    # boundary. A refresh recorded 30 min ago (6:30pm) is still fresh.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 15, 19, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 18, 30, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_weekly(last_refreshed) is False


def test_should_refresh_weekly_true_once_next_boundary_passes(monkeypatch):
    # Same last-refresh timestamp as above, but now it's the FOLLOWING
    # Tuesday evening - a new weekly boundary has passed since then.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 22, 19, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 18, 30, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_weekly(last_refreshed) is True


def test_should_refresh_weekly_false_midweek_after_tuesday_refresh(monkeypatch):
    # Friday of the same week as a Tuesday evening refresh - no new
    # boundary has passed yet, should stay frozen until next Tuesday.
    _freeze_now(monkeypatch, datetime.datetime(2026, 9, 18, 12, 0, tzinfo=PACIFIC))
    last_refreshed = datetime.datetime(2026, 9, 15, 18, 30, tzinfo=PACIFIC).astimezone(datetime.timezone.utc).isoformat()
    assert should_refresh_weekly(last_refreshed) is False
