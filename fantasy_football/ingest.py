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

from . import config, db
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
                        "eligible_slots": json.dumps(bp.eligibleSlots),
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
        # Matchup.home_team/.away_team are bare type-hints with no default -
        # if a side has no scheduled opponent (bye week), the attribute is
        # never set at all and a direct access raises AttributeError. Use
        # getattr so one bye entry doesn't take down the whole week.
        home_team = getattr(m, "home_team", None)
        away_team = getattr(m, "away_team", None)
        home_pk = (
            team_pk_by_espn_id.get(home_team.team_id) if isinstance(home_team, ESPNTeam) else None
        )
        away_pk = (
            team_pk_by_espn_id.get(away_team.team_id) if isinstance(away_team, ESPNTeam) else None
        )

        if home_pk is not None and away_pk is not None:
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

        # Record a score for whichever side(s) are real teams, even on a
        # bye week with no opponent - the team still put up a real score
        # that season totals / all-play calcs need.
        for team_pk, score in ((home_pk, m.home_score), (away_pk, m.away_score)):
            if team_pk is None:
                continue
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


def ingest_future_schedule(
    conn, league, season: int, team_pk_by_espn_id: dict, from_week: int, through_week: int, log=print
) -> None:
    """Pull just the pairing (no scores) for not-yet-played REGULAR SEASON
    weeks of the CURRENT season, so the Matchups page can show upcoming
    games and a win-probability projection. Only regular season - playoff
    pairings aren't real fixed matchups until the bracket is seeded, so
    showing a "future" playoff matchup here would be fabricating a game
    that might never happen. Scores are stored as NULL (not 0 - a 0 would
    look like a real, very bad score), completed is always 0."""
    for week in range(from_week, through_week + 1):
        try:
            matchups = league.scoreboard(week)
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] season {season} future week {week}: {exc}")
            continue
        for m in matchups:
            home_team = getattr(m, "home_team", None)
            away_team = getattr(m, "away_team", None)
            home_pk = (
                team_pk_by_espn_id.get(home_team.team_id) if isinstance(home_team, ESPNTeam) else None
            )
            away_pk = (
                team_pk_by_espn_id.get(away_team.team_id) if isinstance(away_team, ESPNTeam) else None
            )
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
                    "home_score": None,
                    "away_score": None,
                    "is_playoff": 0,
                    "matchup_type": "NONE",
                    "completed": 0,
                },
                conflict_cols=["season_id", "week", "home_team_pk", "away_team_pk"],
            )


def ingest_player_rankings(conn, league, season: int, week: int, log=print) -> None:
    """Snapshot of ESPN's positional rank + ownership% for every rostered
    player, as of right now - used for the Roster Strength metric's
    "season-long rank" signal. Only meaningful for the CURRENT week (ESPN
    doesn't expose historical posRank), so this only ever fills in going
    forward from whenever this feature first ran - no backfill possible,
    same category of limitation as recent_activity(). One batched
    player_info() call covers every rostered player (confirmed: 207
    players in ~1s), not one call per player."""
    player_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT player_id FROM weekly_rosters WHERE season_id = ? AND week = ?",
            (season, week),
        ).fetchall()
    ]
    if not player_ids:
        return
    try:
        players = league.player_info(playerId=player_ids)
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] season {season} week {week} player rankings: {exc}")
        return
    if players is None:
        return
    if not isinstance(players, list):
        players = [players]

    for p in players:
        pos_rank = p.posRank if isinstance(p.posRank, int) and p.posRank > 0 else None
        db.upsert(
            conn,
            "player_rankings",
            {
                "season_id": season,
                "week": week,
                "player_id": p.playerId,
                "pos_rank": pos_rank,
                "percent_owned": p.percent_owned if p.percent_owned != -1 else None,
                "percent_started": p.percent_started if p.percent_started != -1 else None,
            },
            conflict_cols=["season_id", "week", "player_id"],
        )


def ingest_fantasypros_rankings(conn, season: int, week: int, api_key: str, log=print) -> None:
    """Rest-of-season consensus rankings from FantasyPros' licensed API,
    matched to our ESPN player_id and stored only for players actually
    rostered this season/week (FantasyPros' own lists cover the whole
    league-wide player pool per position, most of which nobody in this
    league has rostered). Matching prefers FantasyPros' OWN espn_id
    cross-reference (they support ESPN league sync, so they maintain
    this mapping themselves - see fantasypros_client.fetch_player_
    espn_id_map()), falling back to name/team matching only for anyone
    that cross-reference doesn't cover (player_matching.py). Same
    "current week only" limitation as ingest_player_rankings -
    FantasyPros' ROS endpoint has no history either, so this can't be
    backfilled to past weeks."""
    from . import fantasypros_client, player_matching

    rows = conn.execute(
        """
        SELECT DISTINCT wr.player_id, p.player_name, p.default_position, wr.pro_team
        FROM weekly_rosters wr JOIN players p ON p.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ?
        """,
        (season, week),
    ).fetchall()
    if not rows:
        return
    espn_players_by_position: dict[str, list[dict]] = {}
    for player_id, player_name, position, pro_team in rows:
        espn_players_by_position.setdefault(position, []).append(
            {"player_id": player_id, "player_name": player_name, "pro_team": pro_team}
        )

    try:
        fp_by_position = fantasypros_client.fetch_all_ros_rankings(api_key, season)
        espn_id_map = fantasypros_client.fetch_player_espn_id_map(api_key)
    except Exception as exc:  # noqa: BLE001
        log(f"[warn] season {season} week {week} fantasypros rankings: {exc}")
        return

    matched_total = 0
    for fp_position, fp_players in fp_by_position.items():
        espn_position = player_matching.POSITION_MAP.get(fp_position, fp_position)
        espn_players = espn_players_by_position.get(espn_position, [])
        id_map = player_matching.match_players_for_position(
            espn_players, fp_players, espn_position, espn_id_map=espn_id_map
        )
        fp_by_id = {p["player_id"]: p for p in fp_players}
        for fp_id, espn_id in id_map.items():
            fp = fp_by_id[fp_id]
            db.upsert(
                conn,
                "fantasypros_rankings",
                {
                    "season_id": season,
                    "week": week,
                    "player_id": espn_id,
                    "position": espn_position,
                    "rank_ecr": fp.get("rank_ecr"),
                    "pos_rank": player_matching.parse_pos_rank(fp.get("pos_rank")),
                    "ros_points": fp.get("r2p_pts"),
                },
                conflict_cols=["season_id", "week", "player_id"],
            )
        matched_total += len(id_map)
    log(f"[info] season {season} week {week}: matched {matched_total} FantasyPros ROS rankings to rostered players")


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
        reg_season_count = league.settings.reg_season_count
        if last_week < reg_season_count:
            try:
                ingest_future_schedule(
                    conn, league, season, team_pk_by_espn_id,
                    from_week=last_week + 1, through_week=reg_season_count, log=log,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"[warn] season {season} future schedule: {exc}")

        try:
            ingest_recent_activity(conn, league, season, team_pk_by_espn_id)
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] season {season} recent_activity: {exc}")

        try:
            ingest_player_rankings(conn, league, season, last_week, log=log)
        except Exception as exc:  # noqa: BLE001
            log(f"[warn] season {season} player rankings: {exc}")

        fp_api_key = config.fantasypros_api_key()
        if fp_api_key:
            try:
                ingest_fantasypros_rankings(conn, season, last_week, fp_api_key, log=log)
            except Exception as exc:  # noqa: BLE001
                log(f"[warn] season {season} fantasypros rankings: {exc}")

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
