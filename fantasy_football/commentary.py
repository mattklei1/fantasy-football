"""Weekly recap generation. Consumes structured facts computed elsewhere
(metrics/weekly_awards.py, metrics_weekly, standings) - never computes
FANTASY stats itself, per the project's core rule that Claude is used
ONLY for written commentary, never for calculating anything. The one
deliberate exception (2026-09-13, user-requested): the BAD BEAT section
gives Claude a real web_search tool to look up actual NFL news for that
real-world week and correlate it against `starting_rosters` (a plain,
deterministic roster+score lookup we already compute) - this is Claude
finding and reporting REAL external facts, not calculating a fantasy
stat, so it doesn't violate the rule above. Two writers:

- generate_placeholder_commentary(): deterministic, no API key needed -
  the app must fully work without ANTHROPIC_API_KEY.
- generate_claude_commentary(): Claude-generated prose in the same
  sections, grounded only in the supplied facts.

Recaps are generated ONCE per (season, week) and stored in
`weekly_recaps` - get_or_generate_weekly_recap() checks there first, so
a page view never re-triggers a Claude call (and never re-spends money)
once a recap exists; call it with force_regenerate=True to explicitly
replace one.

Deliberately does its own lightweight SQL (like metrics/loaders.py and
ingest.py) rather than importing dashboard_data.py - this module has to
work from a plain script/cron job too, not just inside a Streamlit
session, so it stays Streamlit-free.
"""
from __future__ import annotations

import datetime
import json
import sqlite3

import pandas as pd

from . import config, db
from .metrics.loaders import load_matchups, load_roster_with_points, load_weekly_scores
from .metrics.weekly_awards import compute_weekly_awards
from .metrics.win_probability import MIN_STDEV, TeamProjection, expected_score, win_probability

CLAUDE_MODEL = "claude-opus-5"
MAX_TOKENS = 4096

SECTION_ORDER = [
    "HEADLINE",
    "GAME OF THE WEEK",
    "BEATDOWN OF THE WEEK",
    "BAD BEAT",
    "MANAGER OF THE WEEK",
    "COACHING DISASTER",
    "FRAUD WATCH",
    "POWER RANKING MOVERS",
    "NEXT WEEK'S GAME TO WATCH",
]


def _team_names(conn: sqlite3.Connection, season: int) -> dict[int, dict]:
    # db.manager_full_name_sql(), NOT mgr.display_name directly - the
    # latter is ESPN's raw account username (e.g. "yankeesjets247"), a
    # real bug found 2026-09-13 leaking usernames into recap prose.
    query = f"""
        SELECT t.id AS team_pk, t.team_name, {db.manager_full_name_sql("mgr")} AS manager_name
        FROM teams t {db.primary_owner_join_sql("t", mgr_alias="mgr", owner_alias="owner")}
        WHERE t.season_id = ?
    """
    rows = pd.read_sql_query(query, conn, params=(season,))
    return {int(r.team_pk): {"team_name": r.team_name, "manager_name": r.manager_name} for r in rows.itertuples()}


def _label(names: dict, team_pk: int | None) -> dict | None:
    if team_pk is None:
        return None
    return {"team_pk": team_pk, **names.get(team_pk, {"team_name": "Unknown", "manager_name": "Unknown"})}


def _power_rank_movers(conn: sqlite3.Connection, season: int, week: int, names: dict) -> list[dict]:
    """Week-over-week Power Score rank change - biggest riser/faller."""
    rows = pd.read_sql_query(
        "SELECT team_pk, power_score FROM metrics_weekly WHERE season_id = ? AND week = ?",
        conn, params=(season, week),
    )
    prev_rows = pd.read_sql_query(
        "SELECT team_pk, power_score FROM metrics_weekly WHERE season_id = ? AND week = ?",
        conn, params=(season, week - 1),
    )
    if rows.empty or prev_rows.empty:
        return []
    rows["rank"] = rows["power_score"].rank(ascending=False, method="min").astype(int)
    prev_rows["prev_rank"] = prev_rows["power_score"].rank(ascending=False, method="min").astype(int)
    merged = rows.merge(prev_rows[["team_pk", "prev_rank"]], on="team_pk", how="inner")
    merged["change"] = merged["prev_rank"] - merged["rank"]
    merged = merged.sort_values("change", ascending=False)
    movers = []
    for _, r in merged.iterrows():
        movers.append({**_label(names, int(r.team_pk)), "rank": int(r["rank"]), "change": int(r["change"])})
    return movers


def _game_to_watch(conn: sqlite3.Connection, season: int, week: int, names: dict) -> dict | None:
    """Next week's projected closest game, using the same expected_score/
    win_probability model as the Matchups page (metrics/win_probability.py),
    projected from stats as of the week just completed."""
    next_week_matchups = pd.read_sql_query(
        "SELECT home_team_pk, away_team_pk FROM matchups "
        "WHERE season_id = ? AND week = ? AND matchup_type = 'NONE'",
        conn, params=(season, week + 1),
    )
    if next_week_matchups.empty:
        return None
    stats = pd.read_sql_query(
        "SELECT team_pk, ppg, last3_ppg FROM metrics_weekly WHERE season_id = ? AND week = ?",
        conn, params=(season, week),
    ).set_index("team_pk")
    scores = load_weekly_scores(conn, season)
    stdevs = scores.groupby("team_pk")["score"].std(ddof=1).fillna(0.0) if not scores.empty else pd.Series(dtype=float)

    best = None
    for r in next_week_matchups.itertuples():
        if r.home_team_pk not in stats.index or r.away_team_pk not in stats.index:
            continue
        home = TeamProjection(
            team_pk=r.home_team_pk,
            expected_score=expected_score(stats.loc[r.home_team_pk, "ppg"], stats.loc[r.home_team_pk, "last3_ppg"]),
            stdev=max(stdevs.get(r.home_team_pk, 0.0), MIN_STDEV),
        )
        away = TeamProjection(
            team_pk=r.away_team_pk,
            expected_score=expected_score(stats.loc[r.away_team_pk, "ppg"], stats.loc[r.away_team_pk, "last3_ppg"]),
            stdev=max(stdevs.get(r.away_team_pk, 0.0), MIN_STDEV),
        )
        prob = win_probability(home, away)
        closeness = abs(prob - 0.5)
        if best is None or closeness < best["closeness"]:
            best = {
                "home": _label(names, r.home_team_pk),
                "away": _label(names, r.away_team_pk),
                "home_win_probability": round(prob, 3),
                "closeness": closeness,
            }
    if best:
        best.pop("closeness")
    return best


def _starting_rosters(conn: sqlite3.Connection, season: int, week: int, names: dict) -> list[dict]:
    """Real starting lineups for the week (team, player name/position/
    points) - not a calculated stat, a plain roster+score lookup. Exists
    so the BAD BEAT section's web-search step (see _build_claude_prompt)
    has real, ground-truth player names to check real NFL news against,
    rather than guessing who was rostered. Empty list (not an error) for
    seasons/weeks with no eligible_slots-era roster data."""
    query = """
        SELECT wr.team_pk, p.player_name, p.default_position AS position,
               COALESCE(pws.points, 0) AS points
        FROM weekly_rosters wr
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN player_week_scores pws
            ON pws.season_id = wr.season_id AND pws.week = wr.week AND pws.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ? AND wr.is_starter = 1
    """
    rows = pd.read_sql_query(query, conn, params=(season, week))
    if rows.empty:
        return []
    rosters = []
    for team_pk, group in rows.groupby("team_pk"):
        rosters.append(
            {
                "team": _label(names, int(team_pk)),
                "players": [
                    {"name": r.player_name, "position": r.position, "points": round(float(r.points), 1)}
                    for r in group.itertuples()
                ],
            }
        )
    return rosters


def build_weekly_facts(conn: sqlite3.Connection, season: int, week: int) -> dict | None:
    """Structured facts for one completed regular-season week. Returns
    None if that week hasn't completed yet (nothing to recap) - never
    fabricates a recap from partial data."""
    completed = conn.execute(
        "SELECT COUNT(*) FROM weekly_team_scores WHERE season_id = ? AND week = ? AND completed = 1 AND is_playoff = 0",
        (season, week),
    ).fetchone()[0]
    if not completed:
        return None

    names = _team_names(conn, season)

    scores_df = load_weekly_scores(conn, season)
    scores_week = scores_df[scores_df["week"] == week]
    matchups_df = load_matchups(conn, season)
    matchups_week = matchups_df[matchups_df["week"] == week]

    season_row = conn.execute(
        "SELECT position_slot_counts FROM seasons WHERE season_id = ?", (season,)
    ).fetchone()
    position_slot_counts = json.loads(season_row[0]) if season_row and season_row[0] else None

    roster_df = load_roster_with_points(conn, season)
    roster_week = roster_df[roster_df["week"] == week] if not roster_df.empty else roster_df

    raw_awards = compute_weekly_awards(scores_week, matchups_week, roster_week, position_slot_counts)
    awards = {}
    for key, value in raw_awards.items():
        if value is None:
            awards[key] = None
            continue
        entry = dict(value)
        if "team_pk" in entry:
            entry["team"] = _label(names, entry["team_pk"])
        if "home_team_pk" in entry and "away_team_pk" in entry:
            entry["home"] = _label(names, entry["home_team_pk"])
            entry["away"] = _label(names, entry["away_team_pk"])
        awards[key] = entry

    fraud_rows = pd.read_sql_query(
        "SELECT team_pk, fraud_index, power_score FROM metrics_weekly WHERE season_id = ? AND week = ? "
        "ORDER BY fraud_index DESC LIMIT 1",
        conn, params=(season, week),
    )
    biggest_fraud = None
    if not fraud_rows.empty:
        r = fraud_rows.iloc[0]
        biggest_fraud = {"team": _label(names, int(r.team_pk)), "fraud_index": round(float(r.fraud_index), 3)}

    return {
        "season": season,
        "week": week,
        "awards": awards,
        "biggest_fraud": biggest_fraud,
        "power_rank_movers": _power_rank_movers(conn, season, week, names),
        "next_week_game_to_watch": _game_to_watch(conn, season, week, names),
        "starting_rosters": _starting_rosters(conn, season, week, names),
    }


def _fmt_team(t: dict | None) -> str:
    if not t:
        return "nobody"
    return f"{t['team_name']} ({t['manager_name']})"


def generate_placeholder_commentary(facts: dict) -> str:
    """Deterministic, no API key required - plain factual sentences per
    section, same section headers Claude is asked to use, so the two are
    interchangeable from the page's point of view. Each header is its
    own bolded paragraph (blank-line separated from its content) so it
    renders as a real line break in Markdown - a single "\\n" collapses
    to a space in CommonMark, which would run the header into the text."""
    a = facts["awards"]
    lines = []

    def section(header: str, *content_lines: str) -> None:
        lines.append(f"**{header}**")
        lines.append("")
        lines.extend(content_lines or ("",))
        lines.append("")

    hs = a.get("highest_score")
    section(
        "HEADLINE",
        f"Week {facts['week']} is in the books - {_fmt_team(hs and hs.get('team'))} led all scorers.",
    )

    cg = a.get("closest_game")
    section(
        "GAME OF THE WEEK",
        f"{_fmt_team(cg['home'])} {cg['home_score']:.1f} - {cg['away_score']:.1f} {_fmt_team(cg['away'])} "
        f"(margin: {cg['margin']:.1f})" if cg else "No games played this week.",
    )

    bo = a.get("biggest_blowout")
    section(
        "BEATDOWN OF THE WEEK",
        f"{_fmt_team(bo['home'])} {bo['home_score']:.1f} - {bo['away_score']:.1f} {_fmt_team(bo['away'])} "
        f"(margin: {bo['margin']:.1f})" if bo else "No games played this week.",
    )

    bb = a.get("bad_beat")
    section(
        "BAD BEAT",
        f"{_fmt_team(bb['team'])} scored {bb['score']:.1f} and still lost." if bb else "No qualifying bad beat this week.",
    )

    mow = a.get("manager_of_the_week")
    section(
        "MANAGER OF THE WEEK",
        f"{_fmt_team(mow['team'])} won with {mow['score']:.1f} points, tops among winners." if mow else "No winner to crown this week.",
    )

    cd = a.get("coaching_disaster")
    if cd:
        verb = "cost them the win" if cd["flipped_result"] else "didn't change the result, but still stung"
        cd_text = f"{_fmt_team(cd['team'])} left {cd['points_left_on_bench']:.1f} points on the bench - {verb}."
    else:
        cd_text = "No lineup data available this week."
    section("COACHING DISASTER", cd_text)

    fw = facts.get("biggest_fraud")
    section(
        "FRAUD WATCH",
        f"{_fmt_team(fw['team'])} has the largest gap between their record and their all-play record." if fw else "Nothing to report.",
    )

    movers = facts.get("power_rank_movers") or []
    if movers:
        riser, faller = movers[0], movers[-1]
        section(
            "POWER RANKING MOVERS",
            f"Riser: {_fmt_team(riser)} (now #{riser['rank']}, {riser['change']:+d})",
            f"Faller: {_fmt_team(faller)} (now #{faller['rank']}, {faller['change']:+d})",
        )
    else:
        section("POWER RANKING MOVERS", "Not enough history yet to track movers.")

    gtw = facts.get("next_week_game_to_watch")
    section(
        "NEXT WEEK'S GAME TO WATCH",
        f"{_fmt_team(gtw['home'])} vs {_fmt_team(gtw['away'])} "
        f"(home win probability: {gtw['home_win_probability']:.0%})" if gtw else "Schedule not available yet.",
    )

    return "\n".join(lines).strip()


def _build_claude_prompt(facts: dict) -> str:
    season, week = facts["season"], facts["week"]
    bad_beat_instructions = (
        "For the BAD BEAT section specifically, you have a `web_search` tool - use it to look up REAL NFL "
        f"news from the actual {season} NFL season, Week {week} (in-game injuries, overturned/reviewed "
        "plays, garbage-time or kneel-down finishes, officiating controversies, or any other real 'bad "
        "beat' storyline from that week's real games). Then check `starting_rosters` below: if a player "
        "named in a real search result is ALSO a player some team here actually STARTED that week, name "
        "them, describe only what your search results actually support (never embellish beyond them), and "
        "connect it to that team/manager's real result that week. Only make this connection when a "
        "rostered player's name is an unambiguous match to what you found via search - if nothing search-"
        "worthy correlates to a rostered player this week, fall back to `awards.bad_beat` (highest score "
        "among that week's losing teams) instead of forcing a connection that isn't real. Do not narrate "
        "your search process (no \"I'll search for...\") - output only the final recap text below."
    )
    return (
        "You are writing a fantasy football weekly recap for a private league. Tone: ESPN/The Athletic "
        "crossed with friendly group-chat trash talk - funny, punchy, concise. Base every claim about "
        "THIS LEAGUE'S stats ONLY on the JSON facts below - NEVER invent a fantasy stat, score, or name "
        "not present in the JSON. The one exception is BAD BEAT, where real web search results are "
        "allowed (see instructions below) - even there, never fabricate a search result. If a section's "
        "data is null/missing, skip that section's details gracefully (don't pretend it exists).\n\n"
        "Write exactly these sections, in this order, using these exact headers formatted as Markdown bold "
        "on their own line (e.g. \"**HEADLINE**\"), followed by a blank line before that section's text:\n"
        + "\n".join(SECTION_ORDER)
        + "\n\n" + bad_beat_instructions
        + "\n\nFACTS (JSON):\n"
        + json.dumps(facts, indent=2)
    )


def _strip_preamble(text: str) -> str:
    """The web-search tool loop can emit narration text ("I'll search
    for...") as its own `text` content block before the final answer -
    generate_claude_commentary() concatenates all text blocks in order,
    so trim anything before the first real section header rather than
    relying solely on the prompt's "don't narrate" instruction."""
    anchor = f"**{SECTION_ORDER[0]}**"
    idx = text.find(anchor)
    return text[idx:] if idx > 0 else text


def generate_claude_commentary(facts: dict, api_key: str) -> str:
    """Raises on any failure - get_or_generate_weekly_recap() catches
    broadly and falls back to the placeholder, per the spec's "must work
    without an API key" requirement extended to "must degrade gracefully
    if the API call fails too". Includes Anthropic's server-side
    web_search tool (see module docstring) so the BAD BEAT section can
    ground itself in real NFL news - capped at 3 searches, more than
    enough for one well-scoped "what happened this week" question per
    Anthropic's own sizing guidance."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": _build_claude_prompt(facts)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined to generate this recap: {response.stop_details}")
    text = _strip_preamble("".join(block.text for block in response.content if block.type == "text"))
    if not text.strip():
        raise RuntimeError("Claude returned an empty recap")
    return text


def get_or_generate_weekly_recap(
    conn: sqlite3.Connection, season: int, week: int, force_regenerate: bool = False, log=print
) -> dict | None:
    """Returns {"facts": dict, "commentary": str, "source": str,
    "generated_at": str} or None if the week hasn't completed yet.
    Checks weekly_recaps first (unless force_regenerate) so repeat page
    views never re-trigger a Claude call."""
    if not force_regenerate:
        row = conn.execute(
            "SELECT facts_json, commentary_text, source, generated_at FROM weekly_recaps "
            "WHERE season_id = ? AND week = ?",
            (season, week),
        ).fetchone()
        if row:
            facts_json, commentary_text, source, generated_at = row
            return {
                "facts": json.loads(facts_json),
                "commentary": commentary_text,
                "source": source,
                "generated_at": generated_at,
            }

    facts = build_weekly_facts(conn, season, week)
    if facts is None:
        return None

    api_key = config.anthropic_api_key()
    source = "placeholder"
    commentary = None
    if api_key:
        try:
            commentary = generate_claude_commentary(facts, api_key)
            source = "claude"
        except Exception as exc:  # noqa: BLE001 - any failure falls back, never crashes the page
            log(f"[warn] season {season} week {week} Claude commentary failed, using placeholder: {exc}")
    if commentary is None:
        commentary = generate_placeholder_commentary(facts)

    generated_at = datetime.datetime.utcnow().isoformat(timespec="seconds")
    db.upsert(
        conn,
        "weekly_recaps",
        {
            "season_id": season,
            "week": week,
            "facts_json": json.dumps(facts),
            "commentary_text": commentary,
            "source": source,
            "generated_at": generated_at,
        },
        conflict_cols=["season_id", "week"],
    )
    conn.commit()
    return {"facts": facts, "commentary": commentary, "source": source, "generated_at": generated_at}
