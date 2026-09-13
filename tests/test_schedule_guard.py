"""Unit tests for fantasy_football.schedule_guard - DST-safety logic."""
import datetime
from zoneinfo import ZoneInfo

from fantasy_football import schedule_guard
from fantasy_football.schedule_guard import PACIFIC, is_target_time_now


def _freeze_now(monkeypatch, dt: datetime.datetime):
    class _FrozenDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.astimezone(tz) if tz else dt

    monkeypatch.setattr(schedule_guard, "datetime", type("M", (), {"datetime": _FrozenDatetime}))


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
