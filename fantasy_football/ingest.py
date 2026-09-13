"""ESPN -> SQLite ingestion. All functions are idempotent (safe to re-run):
every write goes through db.upsert against a UNIQUE constraint, so refreshing
never creates duplicate rows.

Cross-season note: this league has changed rules over time (PPR value,
1QB vs superflex, flex slot count, and a top-half/median bonus win format
added in 2025 - see `seasons.median_scoring`, `.reception_points`,
`.position_slot_counts`). Raw points_for/points_against are therefore NOT
directly comparable across seasons with different settings. This module
only ingests raw facts; metrics (Phase 3) must normalize before making any
cross-season claim (e.g. season-relative percentile/z-score rather than
raw point totals for Hall of Fame "best season" style comparisons).
"""
from __future__ import annotations

import datetime
import json
from typing import Optional

from espn_api.football.team import Team as ESPNTeam

from . import db
from .espn_client import ESPNClient

BENCH_SLOTS = {"BE", "IR"}


def _utcnow_iso() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds")


def _upsert_manager(conn, member: dict) -> None:
    db.upsert(
        conn,
        "managers",
        {
            "manager_id": member["id"],
            "display_name": member.get("displayName") or "",
            "first_name": member.get("firstName") or "",
            "last_name": member.get("lastName") or "",
        },
        conflict_cols=["manager_id"],
    )


def ingest_managers(conn, league) -> None:
    for member in league.members:
        _upsert_manager(conn, member)


def ingest_season_row(conn, league, season: int, current_season: int) -> None:
    settings = league.settings
    reception_points = None
    for item in settings.scoring_format:
        if item.get("id") == 53:  # "Each reception"
            reception_points = item.get("points")
            break
    if reception_points is None:
        reception_points = 0.0  # no reception scoring item present = standard, not PPR

    db.upsert(
        conn,
        "seasons",
        {
            "season_id": season,
            "league_id": league.league_id,
            "league_name": settings.name,
            "current_week": league.current_week,
            "reg_season_count": settings.reg_season_count,
            "playoff_team_count": settings.playoff_team_count,
            "scoring_type": settings.scoring_type,
            "median_scoring": int(bool(settings.median_scoring)),
            "reception_points": reception_points,
            "position_slot_counts": json.dumps(settings.position_slot_counts),
            "scoring_format_json": json.dumps(settings.scoring_format),
            "is_current": int(season == current_season),
            "last_ingested_at": _utcnow_iso(),
        },
        conflict_cols=["season_id"],
    )


def ingest_teams(conn, league, season: int) -> dict[int, int]:
    """Upsert teams + owners for this season. Returns espn_team_id -> team_pk."""
    team_pk_by_espn_id: dict[int, int] = {}
    for team in league.teams:
        db.upsert(
            conn,
            "teams",
            {
                "season_id": season,
                "espn_team_id": team.team_id,
                "team_name": team.team_name,
                "team_abbrev": team.team_abbrev,
                "division_id": team.division_id,
                "division_name": team.division_name,
                "wins": team.wins,
                "losses": team.losses,
                "ties": team.ties,
                "points_for": team.points_for,
                "points_against": team.points_against,
                "standing": team.standing,
                "final_standing": team.final_standing,
                "streak_type": team.streak_type,
                "streak_length": team.streak_length,
            },
            conflict_cols=["season_id", "espn_team_id"],
        )
        team_pk = db.get_team_pk(conn, season, team.team_id)
        team_pk_by_espn_id[team.team_id] = team_pk

        for owner in team.owners:
            _upsert_manager(conn, owner)
            conn.execute(
                "INSERT OR IGNORE INTO team_owners (team_pk, manager_id) VALUES (?, ?)",
                (team_pk, owner["id"]),
            )
    return team_pk_by_espn_id


def _upsert_player(conn, player_id: int, name: str, position: str) -> None:
    db.upsert(
        conn,
        "players",
        {"player_id": player_id, "player_name": name, "default_position": position},
        conflict_cols=["player_id"],
    )


def ingest_week_boxscores(
    conn, league, season: int, week: int, team_pk_by_espn_id: dict, completed: bool
) -> None:
    boxes = league.box_scores(week)
    for box in boxes:
        home_team, away_team = box.home_team, box.away_team
        if home_team is None or away_team is None:
            continue  # bye week - no real matchup to record
        home_pk = team_pk_by_espn_id.get(home_team.team_id)
        away_pk = team_pk_by_espn_id.get(away_team.team_id)
        if home_pk is None or away_pk is None:
            continue

        db.upsert(
            conn,
            "matchups",
            {
                "season_id": season,
                "week": week,
                "home_team_pk": home_pk,
                "away_team_pk": away_pk,
                "home_score": box.home_score,
                "away_score": box.away_score,
                "is_playoff": int(box.is_playoff),
                "matchup_type": box.matchup_type,
                "completed": int(completed),
            },
            conflict_cols=["season_id", "week", "home_team_pk", "away_team_pk"],
        )

        for team_pk, score, projected, lineup in (
            (home_pk, box.home_score, box.home_projected, box.home_lineup),
            (away_pk, box.away_score, box.away_projected, box.away_lineup),
        ):
            db.upsert(
                conn,
                "weekly_team_scores",
                {
                    "season_id": season,
                    "week": week,
                    "team_pk": team_pk,
                    "score": score,
                    "projected_score": projected,
                    "is_playoff": int(box.is_playoff),
                    "completed": int(completed),
                },
                conflict_cols=["season_id", "week", "team_pk"],
            )

            for bp in lineup:
                _upsert_player(conn, bp.playerId, bp.name, bp.position)

                db.upsert(
                    conn,
                    "weekly_rosters",
                    {
                        "season_id": season,
                        "week": week,
                        "team_pk": team_pk,
                        "player_id": bp.playerId,
                        "slot_position": bp.slot_position,
                        "pro_team": bp.proTeam,
                        "is_starter": int(bp.slot_position not in BENCH_SLOTS),
                    },
                    conflict_cols=["season_id", "week", "team_pk", "player_id"],
                )

                db.upsert(
                    conn,
                    "player_week_scores",
                    {
                        "season_id": season,
                        "week": week,
                        "player_id": bp.playerId,
                        "points": bp.points,
                        "projected_points": bp.projected_points,
                    },
                    conflict_cols=["season_id", "week", "player_id"],
                )


def ingest_week_scoreboard(
    conn, league, season: int, week: int, team_pk_by_espn_id: dict, completed: bool
) -> None:
    """Fallback for seasons before 2019, when box_scores() (player detail)
    isn't available - matchup/team scores only, no roster/player rows."""
    matchups = league.scoreboard(week)
    for m in matchups:
        home_team, away_team = m.home_team, m.away_team
        if not isinstance(home_team, ESPNTeam) or not isinstance(away_team, ESPNTeam):
            continue  # bye week - unmatched raw team id
        home_pk = team_pk_by_espn_id.get(home_team.team_id)
        away_pk = team_pk_by_espn_id.get(away_team.team_id)
        if home_pk is None or away_pk is None:
            continue

        db.upsert(
            conn,
            "matchups",
            {
                "season_id": season,
                "week": week,
                "home_team_pk": home_pk,
                "away_team_pk": away_pk,
                "home_score": m.home_score,
                "away_score": m.away_score,
                "is_playoff": int(m.is_playoff),
                "matchup_type": m.matchup_type,
                "completed": int(completed),
            },
            conflict_cols=["season_id", "week", "home_team_pk", "away_team_pk"],
        )
        for team_pk, score in ((home_pk, m.home_score), (away_pk, m.away_score)):
            db.upsert(
                conn,
                "weekly_team_scores",
                {
                    "season_id": season,
                    "week": week,
                    "team_pk": team_pk,
                    "score": score,
                    "projected_score": None,
                    "is_playoff": int(m.is_playoff),
                    "completed": int(completed),
                },
                conflict_cols=["season_id", "week", "team_pk"],
            )


def ingest_draft(conn, league, season: int, team_pk_by_espn_id: dict) -> None:
    for overall_pick, pick in enumerate(league.draft, start=1):
        team_pk = team_pk_by_espn_id.get(pick.team.team_id) if pick.team else None
        nominating_pk = (
            team_pk_by_espn_id.get(pick.nominatingTeam.team_id) if pick.nominatingTeam else None
        )
        db.upsert(
            conn,
            "draft_picks",
            {
                "season_id": season,
                "team_pk": team_pk,
                "nominating_team_pk": nominating_pk,
                "player_id": pick.playerId,
                "player_name": pick.playerName,
                "round_num": pick.round_num,
                "round_pick": pick.round_pick,
                "overall_pick": overall_pick,
                "bid_amount": pick.bid_amount,
                "keeper_status": int(bool(pick.keeper_status)),
            },
            conflict_cols=["season_id", "round_num", "round_pick"],
        )


def ingest_recent_activity(
    conn, league, season: int, team_pk_by_espn_id: dict, max_records: int = 1000, page_size: int = 25
) -> None:
    """Only works for the current season - ESPN's communication/activity
    endpoint 404s for prior years (confirmed empirically, not documented).
    Also only ever returns a rolling recent window, not full-season
    history, so this cannot backfill old transactions after the fact."""
    offset = 0
    fetched = 0
    while fetched < max_records:
        batch = league.recent_activity(size=page_size, offset=offset)
        if not batch:
            break
        for activity in batch:
            date_iso = datetime.datetime.utcfromtimestamp(activity.date / 1000).isoformat(
                timespec="seconds"
            )
            for team, action, player, bid_amount in activity.actions:
                team_pk = (
                    team_pk_by_espn_id.get(team.team_id)
                    if isinstance(team, ESPNTeam)
                    else None
                )
                player_id = getattr(player, "playerId", None)
                player_name = getattr(player, "name", None)
                if player_name is None and isinstance(player, str):
                    player_name = player
                db.upsert(
                    conn,
                    "transactions",
                    {
                        "season_id": season,
                        "activity_date": date_iso,
                        "team_pk": team_pk,
                        "action_type": action,
                        "player_id": player_id,
                        "player_name": player_name,
                        "bid_amount": bid_amount or None,
                    },
                    conflict_cols=["season_id", "activity_date", "team_pk", "action_type", "player_id"],
                )
        fetched += len(batch)
        if len(batch) < page_size:
            break
        offset += page_size


def ingest_season(conn, client: ESPNClient, season: int, log=print) -> None:
    league = client.get_league(season)
    current_season = client.credentials.current_season

    ingest_managers(conn, league)
    ingest_season_row(conn, league, season, current_season)
    team_pk_by_espn_id = ingest_teams(conn, league, season)

    last_week = league.current_week
    for week in range(1, last_week + 1):
        completed = (season != current_season) or (week < league.current_week)
        try:
            if season >= 2019:
                ingest_week_boxscores(conn, league, season, week, team_pk_by_espn_id, completed)
            else:
                ingest_week_scoreboard(conn, league, season, week, team_pk_by_espn_id, completed)
        except Exception as exc:  # noqa: BLE001 - log and keep ingesting other weeks
            log(f"[warn] season {season} week {week}: {exc}")

    try:
        ingest_draft(conn, league, season, team_pk_by_espn_id)
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] season {season} draft: {exc}")

    if season == current_season:
        try:
            ingest_recent_activity(conn, league, season, team_pk_by_espn_id)
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] season {season} recent_activity: {exc}")

    conn.commit()


def refresh_all(
    client: Optional[ESPNClient] = None,
    seasons: Optional[list[int]] = None,
    log=print,
) -> str:
    """Ingest every requested season (default: all seasons ESPN reports as
    available) and record the run in refresh_log. Returns the run status."""
    client = client or ESPNClient()
    conn = db.get_connection()
    db.init_db(conn)

    seasons = seasons if seasons is not None else client.available_seasons()
    started_at = _utcnow_iso()
    status = "success"
    detail_lines = []

    for season in seasons:
        try:
            log(f"Ingesting season {season}...")
            ingest_season(conn, client, season, log=log)
            detail_lines.append(f"{season}: ok")
        except Exception as exc:  # noqa: BLE001 - one bad season shouldn't kill the run
            status = "partial"
            detail_lines.append(f"{season}: FAILED ({exc})")
            log(f"[error] season {season} failed: {exc}")

    conn.execute(
        "INSERT INTO refresh_log (started_at, finished_at, status, seasons_refreshed, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (started_at, _utcnow_iso(), status, ",".join(str(s) for s in seasons), "\n".join(detail_lines)),
    )
    conn.commit()
    conn.close()
    return status
