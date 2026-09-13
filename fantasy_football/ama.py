"""Ask Me Anything: a natural-language Q&A bar over league history/
rosters/stats, powered by Gemini Flash. Two-step flow per question:

1. generate_sql() - Gemini turns the question into ONE read-only SQL
   query against the `ama_*` views (see db.py's _create_ama_views and
   this module's SCHEMA_DESCRIPTION - both deliberately exclude
   `fantasypros_rankings`).
2. ama_query.run_readonly_query() executes it under the sandboxed,
   authorizer-enforced connection (the actual security boundary - see
   that module's docstring; this file only builds the prompt/query, it
   does not itself enforce anything).
3. answer_question() - a second Gemini call turns the returned ROWS
   (never the raw SQL, never anything Gemini wasn't explicitly given)
   into a natural-language answer.

Neither this module nor the DB it queries ever holds the FantasyPros API
key, the GroupMe token, or the raw FantasyPros rankings data - those live
only in `.env` (never ingested into any table Ask Me Anything can reach)
and the one table that DOES hold FantasyPros data (`fantasypros_rankings`)
is structurally denied by ama_query's authorizer regardless of anything
this module's prompt says.
"""
from __future__ import annotations

from pydantic import BaseModel

SCHEMA_DESCRIPTION = """
You can query these views only (SQLite). Every view already spans ALL seasons (2015-present) -
filter by season_id yourself when the question is season-specific. "Manager identity" already
accounts for team name changes and ESPN account relinks - manager_name is the stable identity to
group/join on, not team_name, when a question is about a PERSON across time.

ama_seasons(season_id, league_name, current_week, reg_season_count, playoff_team_count,
    scoring_type, median_scoring, reception_points)
    - one row per season. median_scoring=1 means that season used a top-half-of-the-league bonus
      win format (see ama_standings.median_wins/losses) added starting 2025.

ama_teams(team_pk, season_id, team_name, manager_name)
    - one row per team-season. team_pk is only unique within season_id (a manager's team_pk
      changes every season - always join through manager_name for cross-season identity).

ama_matchups(season_id, week, matchup_type, completed, is_playoff,
    home_team_name, home_manager_name, home_score, away_team_name, away_manager_name, away_score)
    - one row per matchup. matchup_type: 'NONE' = regular season, 'WINNERS_BRACKET' = the real
      championship playoff bracket, 'LOSERS_CONSOLATION_LADDER'/'WINNERS_CONSOLATION_LADDER' =
      non-championship playoff consolation games (exclude these for "who won the championship"
      style questions). completed=0 rows are future/unplayed - scores will be NULL.

ama_standings(season_id, week, team_name, manager_name, games_played,
    matchup_wins, matchup_losses, matchup_ties, median_wins, median_losses, median_ties,
    actual_win_pct, points_for, points_against, ppg, last3_ppg, ppg_percentile,
    all_play_wins, all_play_losses, all_play_ties, all_play_win_pct,
    expected_wins, luck_wins, fraud_index, power_score)
    - SEASON-TO-DATE snapshot as of each week (one row per team per week - use MAX(week) per
      season for that season's final standings, or a specific week for a point-in-time view).
      actual_win_pct is the REAL record that determines standings (includes the median bonus in
      seasons that use it). all_play_win_pct = record if you'd played every team every week
      (measures team quality independent of schedule). luck_wins = matchup wins minus all-play
      expected wins (positive = beneficiary of a soft schedule). fraud_index = actual_win_pct
      minus all_play_win_pct (positive = record flatters the team relative to how good they
      actually looked). power_score (0-100) blends all of the above - the closest thing to an
      overall "how good is this team" ranking for that week. ppg_percentile is season-relative
      (0-1) - use this, NOT raw ppg, to compare scoring across different seasons/eras (scoring
      rules changed over the years).

ama_lineup_efficiency(season_id, week, team_name, manager_name,
    lineup_efficiency, actual_starter_points, optimal_starter_points, points_left_on_bench,
    optimal_wins, optimal_losses, optimal_ties, manager_caused_losses,
    correct_decisions, total_decisions, decision_accuracy)
    - season-to-date snapshot as of each week. Only populated for 2019+. lineup_efficiency =
      actual/optimal starter points (how well they set their lineup). decision_accuracy = percent
      of start/sit calls that matched the optimal lineup (points-blind - not skewed by one huge
      bench outlier). manager_caused_losses = games the optimal lineup would have won but the
      real one didn't.

ama_roster_strength(season_id, week, team_name, manager_name,
    starter_value, bench_value, starter_weight, bench_weight, roster_strength)
    - a forward-looking "how good is this roster right now" score (0-100), current-week-only (no
      history) - NOT a measure of past performance, see power_score for that instead.

ama_draft_picks(season_id, team_name, manager_name, player_name,
    round_num, round_pick, overall_pick, bid_amount, keeper_status)

ama_transactions(season_id, activity_date, team_name, manager_name,
    action_type, player_name, bid_amount)
    - waiver/free-agent adds, drops, trades. Only recent history available (ESPN API limit), not
      a full historical log for older seasons.

ama_player_weeks(season_id, week, team_name, manager_name,
    player_name, default_position, is_starter, slot_position, points, projected_points)
    - one row per rostered player per team per week (starters AND bench). is_starter=1 for
      starters. Only 2019+ has reliable slot/eligibility data.

ama_weekly_recaps(season_id, week, commentary_text, source, generated_at)
    - the human-readable weekly recap article already written for that week, if one exists.
""".strip()


class _SqlResponse(BaseModel):
    sql: str


def _client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


def generate_sql(question: str, context: dict, api_key: str, model: str) -> str:
    """Returns the raw SQL text Gemini proposes - NOT yet validated or
    executed (ama_query.run_readonly_query does that, and is the actual
    trust boundary; treat this function's output as untrusted)."""
    from google.genai import types

    prompt = (
        "You are a text-to-SQLite assistant for a private fantasy football league dashboard. "
        "Write EXACTLY ONE read-only SQLite SELECT (or WITH ... SELECT) query that answers the "
        "user's question, using ONLY the views described below. Never reference any table or view "
        "not listed here. If the question can't be answered from this schema, write a query that "
        "returns zero rows rather than guessing.\n\n"
        f"SCHEMA:\n{SCHEMA_DESCRIPTION}\n\n"
        f"CONTEXT: {context}\n\n"
        f"QUESTION: {question}"
    )
    client = _client(api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SqlResponse,
            temperature=0,
        ),
    )
    parsed = _SqlResponse.model_validate_json(response.text)
    return parsed.sql


def answer_question(question: str, rows: list[dict], api_key: str, model: str) -> str:
    """Turns SQL result rows into a natural-language answer. Given ONLY
    the rows (not the raw SQL, not anything else) - grounds the answer to
    exactly what was actually returned."""
    from google.genai import types

    prompt = (
        "You are a friendly, knowledgeable assistant for a private fantasy football league "
        "dashboard. Answer the user's question using ONLY the data rows below - never invent a "
        "stat, name, or number not present in them. If the rows are empty or don't actually answer "
        "the question, say so plainly rather than guessing. Keep it concise and conversational - "
        "don't mention SQL, databases, or queries.\n\n"
        f"QUESTION: {question}\n\n"
        f"DATA ROWS (JSON): {rows}"
    )
    client = _client(api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.3),
    )
    return response.text.strip()


def ask(db_path, question: str, context: dict, api_key: str, model: str) -> dict:
    """Full round trip. Returns {"answer": str, "sql": str, "row_count": int}.
    Raises ama_query.AmaQueryError (safe to display) or a Gemini SDK
    exception (caller should catch broadly and show a generic failure
    message - see pages/8_Ask_Me_Anything.py)."""
    from .ama_query import run_readonly_query

    sql = generate_sql(question, context, api_key, model)
    rows = run_readonly_query(db_path, sql)
    answer = answer_question(question, rows, api_key, model)
    return {"answer": answer, "sql": sql, "row_count": len(rows)}
