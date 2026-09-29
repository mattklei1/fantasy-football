"""Unit tests for fantasy_football.ingest - focused on the pure DB-state
decision logic (which weeks need catching up) rather than the ESPN API
integration itself, which this project has otherwise verified against
live ESPN pulls rather than mocking the third-party espn_api library's
League/BoxScore objects (see PROJECT_BRIEF)."""
import sqlite3

import pytest

from fantasy_football import db, ingest


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    db.init_db(c)
    return c


class _FakeLeague:
    def __init__(self, current_week):
        self.current_week = current_week


def _mark_completed(conn, season, week):
    conn.execute(
        "INSERT INTO weekly_team_scores (season_id, week, team_pk, score, completed, is_playoff) "
        "VALUES (?, ?, 1, 100.0, 1, 0)",
        (season, week),
    )
    conn.commit()


def test_catch_up_completed_weeks_only_ingests_the_newly_finished_week(conn, monkeypatch):
    # DB thinks week 2 is the last completed week, but ESPN's
    # league.current_week has since advanced to 4 - week 3 is the ONE
    # genuinely newly-finished week that needs backfilling; week 4
    # itself is still in progress (never "completed") and weeks 1/2 are
    # already done, so neither should be re-ingested.
    _mark_completed(conn, 2099, 1)
    _mark_completed(conn, 2099, 2)
    calls = []
    monkeypatch.setattr(
        ingest, "ingest_week_boxscores",
        lambda conn, league, season, week, team_pk_by_espn_id, completed: calls.append((week, completed)),
    )
    ingest._catch_up_completed_weeks(conn, _FakeLeague(current_week=4), 2099, {1: 1}, log=lambda *a: None)
    assert calls == [(3, True)]


def test_catch_up_completed_weeks_noop_when_nothing_new(conn, monkeypatch):
    # DB already matches ESPN's current_week - the common case on every
    # page load once a week is caught up - should make zero ESPN calls.
    _mark_completed(conn, 2099, 1)
    calls = []
    monkeypatch.setattr(ingest, "ingest_week_boxscores", lambda *a, **k: calls.append(a))
    ingest._catch_up_completed_weeks(conn, _FakeLeague(current_week=2), 2099, {1: 1}, log=lambda *a: None)
    assert calls == []


def test_catch_up_completed_weeks_backfills_multiple_missed_weeks(conn, monkeypatch):
    # the site sat stale for a few weeks (e.g. nobody visited) - catch-up
    # should cover every missed week, not just the most recent one.
    _mark_completed(conn, 2099, 1)
    calls = []
    monkeypatch.setattr(
        ingest, "ingest_week_boxscores",
        lambda conn, league, season, week, team_pk_by_espn_id, completed: calls.append(week),
    )
    ingest._catch_up_completed_weeks(conn, _FakeLeague(current_week=5), 2099, {1: 1}, log=lambda *a: None)
    assert calls == [2, 3, 4]


def test_catch_up_completed_weeks_tolerates_a_failing_week(conn, monkeypatch):
    # one bad week (a transient ESPN hiccup) shouldn't block catching up
    # the others.
    calls = []

    def _boxscores(conn, league, season, week, team_pk_by_espn_id, completed):
        if week == 1:
            raise RuntimeError("ESPN hiccup")
        calls.append(week)

    monkeypatch.setattr(ingest, "ingest_week_boxscores", _boxscores)
    ingest._catch_up_completed_weeks(conn, _FakeLeague(current_week=3), 2099, {1: 1}, log=lambda *a: None)
    assert calls == [2]
