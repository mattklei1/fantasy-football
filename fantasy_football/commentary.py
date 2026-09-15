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
from .metrics.lineup_optimizer import RosterPlayer, optimal_lineup
from .metrics.loaders import load_matchups, load_roster_with_projections, load_weekly_scores
from .metrics.weekly_awards import compute_weekly_awards
from .metrics.win_probability import MIN_STDEV, TeamProjection, win_probability

#: Standard ESPN roster positions to check for a free-agent fallback in
#: _game_to_watch - a small local duplicate of war_room_data.
#: ESPN_POSITIONS rather than importing that module, which pulls in
#: Streamlit at import time (see this module's own "Streamlit-free"
#: design goal in its docstring).
NEXT_WEEK_POSITIONS = ["QB", "RB", "WR", "TE", "K", "D/ST"]

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


def _free_agent_avg_projection_by_position(league, next_week: int, pool_size: int = 25) -> dict[str, float]:
    """Average next-week ESPN projection among currently available free
    agents at each standard position - a stand-in for "the manager would
    stream a replacement" when a rostered player's own next-week
    projection is 0 (see _team_next_week_optimal_projection). One live
    league.free_agents() call per position, computed ONCE per
    build_weekly_facts() call and reused across every team's projection
    - not refetched per team. Empty dict (not an error) if `league` is
    unavailable or a position's lookup fails; callers degrade to a plain
    0 contribution in that case rather than fabricating a number."""
    result: dict[str, float] = {}
    for position in NEXT_WEEK_POSITIONS:
        try:
            agents = league.free_agents(size=pool_size, position=position)
        except Exception:  # noqa: BLE001 - one bad position shouldn't blank the whole lookup
            continue
        projections = [(a.stats.get(next_week) or {}).get("projected_points") or 0.0 for a in agents]
        projections = [p for p in projections if p > 0]
        if projections:
            result[position] = sum(projections) / len(projections)
    return result


def _team_next_week_optimal_projection(
    conn: sqlite3.Connection, season: int, roster_week: int, next_week: int, team_pk: int,
    position_slot_counts: dict, free_agent_avg_by_position: dict[str, float],
) -> float:
    """This team's projected score for `next_week`, ASSUMING they set an
    optimal lineup from their CURRENT roster (as of `roster_week`, the
    week just completed) using ESPN's own next-week pregame projections
    - not a current-lineup or season-average based number, since nobody
    has actually set next week's lineup yet (user feedback 2026-09-15:
    "don't show me win probability as of now because people haven't set
    their lineups... assume that people sub in people with the highest
    ESPN projections"). Reuses the same eligible_slots-respecting
    optimal_lineup() solver as lineup efficiency - a real, legal lineup,
    never an illegal position swap. Any slot whose assigned rostered
    player still projects to 0 (bye, unprojected, etc.) falls back to
    the average free-agent projection at that player's position - "if
    anyone still has a zero after these substitutions, assume they pick
    someone up off of the waiver wire" (same user feedback)."""
    rows = pd.read_sql_query(
        """
        SELECT wr.player_id, p.default_position AS position, wr.eligible_slots,
               pws_next.projected_points
        FROM weekly_rosters wr
        JOIN players p ON p.player_id = wr.player_id
        LEFT JOIN player_week_scores pws_next
            ON pws_next.season_id = wr.season_id AND pws_next.week = ? AND pws_next.player_id = wr.player_id
        WHERE wr.season_id = ? AND wr.week = ? AND wr.team_pk = ? AND wr.eligible_slots IS NOT NULL
        """,
        conn, params=(next_week, season, roster_week, team_pk),
    )
    if rows.empty:
        return 0.0
    rows["eligible_slots"] = rows["eligible_slots"].apply(lambda s: frozenset(json.loads(s)))
    # A player who simply hasn't had a next-week projection ingested yet
    # (a common, real state - not an error) comes back from the LEFT
    # JOIN as NaN, not None or 0 - `x or 0.0` does NOT catch this (NaN is
    # truthy in Python, so `nan or 0.0` evaluates to nan, not 0.0),
    # silently poisoning the cost matrix below with an invalid entry.
    # fillna() up front instead of relying on `or` at each use site.
    rows["projected_points"] = rows["projected_points"].fillna(0.0)
    players = [
        RosterPlayer(int(r.player_id), float(r.projected_points), r.eligible_slots)
        for r in rows.itertuples()
    ]
    _, assignment = optimal_lineup(players, position_slot_counts)
    position_by_player = dict(zip(rows["player_id"], rows["position"]))
    projected_by_player = dict(zip(rows["player_id"], rows["projected_points"]))

    total = 0.0
    for player_id in assignment.values():
        proj = projected_by_player.get(player_id, 0.0)
        if proj <= 0:
            proj = free_agent_avg_by_position.get(position_by_player.get(player_id), 0.0)
        total += proj
    return total


def _game_to_watch(
    conn: sqlite3.Connection, season: int, week: int, names: dict,
    position_slot_counts: dict | None, league=None,
) -> dict | None:
    """Next week's projected closest game. Each team's projected score
    assumes OPTIMAL lineup management from their current roster using
    next week's ESPN projections (see _team_next_week_optimal_projection)
    rather than a season-PPG blend - deliberately different from the
    Matchups page's in-season win probability, since nobody's actually
    set next week's lineup yet. `league` (a live espn_api League, only
    needed for the free-agent-fallback step) is optional - without it,
    any zero-projection slot just contributes 0 rather than a live-
    ESPN-call-dependent estimate; still a real number, just less
    complete. None if position_slot_counts is unavailable (can't build a
    legal lineup without it) or no matchups are scheduled next week."""
    if not position_slot_counts:
        return None
    next_week = week + 1
    next_week_matchups = pd.read_sql_query(
        "SELECT home_team_pk, away_team_pk FROM matchups "
        "WHERE season_id = ? AND week = ? AND matchup_type = 'NONE'",
        conn, params=(season, next_week),
    )
    if next_week_matchups.empty:
        return None

    scores = load_weekly_scores(conn, season)
    stdevs = scores.groupby("team_pk")["score"].std(ddof=1).fillna(0.0) if not scores.empty else pd.Series(dtype=float)

    free_agent_avg_by_position = (
        _free_agent_avg_projection_by_position(league, next_week) if league is not None else {}
    )

    team_pks = pd.unique(next_week_matchups[["home_team_pk", "away_team_pk"]].to_numpy().ravel())
    projected_by_team = {
        int(tp): _team_next_week_optimal_projection(
            conn, season, week, next_week, int(tp), position_slot_counts, free_agent_avg_by_position
        )
        for tp in team_pks
    }

    best = None
    for r in next_week_matchups.itertuples():
        if r.home_team_pk not in projected_by_team or r.away_team_pk not in projected_by_team:
            continue
        home = TeamProjection(
            team_pk=r.home_team_pk, expected_score=projected_by_team[r.home_team_pk],
            stdev=max(stdevs.get(r.home_team_pk, 0.0), MIN_STDEV),
        )
        away = TeamProjection(
            team_pk=r.away_team_pk, expected_score=projected_by_team[r.away_team_pk],
            stdev=max(stdevs.get(r.away_team_pk, 0.0), MIN_STDEV),
        )
        prob = win_probability(home, away)
        closeness = abs(prob - 0.5)
        if best is None or closeness < best["closeness"]:
            best = {
                "home": _label(names, r.home_team_pk),
                "away": _label(names, r.away_team_pk),
                "home_projected_score": round(projected_by_team[r.home_team_pk], 1),
                "away_projected_score": round(projected_by_team[r.away_team_pk], 1),
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


def build_weekly_facts(conn: sqlite3.Connection, season: int, week: int, league=None) -> dict | None:
    """Structured facts for one completed regular-season week. Returns
    None if that week hasn't completed yet (nothing to recap) - never
    fabricates a recap from partial data. `league` (a live espn_api
    League, optional) only feeds next_week_game_to_watch's free-agent
    fallback (see _game_to_watch) - every other fact here is computed
    from already-ingested DB data, no live ESPN call needed."""
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

    roster_df = load_roster_with_projections(conn, season)
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
        "next_week_game_to_watch": _game_to_watch(conn, season, week, names, position_slot_counts, league=league),
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

    mow = a.get("best_lineup_efficiency")
    mow_lines = [
        f"{_fmt_team(mow['team'])} set the best lineup this week - "
        f"{mow['lineup_efficiency'] * 100:.1f}% of their optimal possible starter points."
        if mow else "No lineup data available this week."
    ]
    slc = a.get("smart_lineup_call")
    if slc:
        mow_lines.append(
            f"Smart call: {_fmt_team(slc['team'])} started {slc['started']['player_name']} "
            f"({slc['started']['points']:.1f} pts) over the higher-projected {slc['benched']['player_name']} "
            f"({slc['benched']['points']:.1f} pts actual) - a {slc['actual_swing']:.1f}-point save."
        )
    section("MANAGER OF THE WEEK", *mow_lines)

    cd = a.get("coaching_disaster")
    if cd:
        consequences = []
        if cd["flipped_result"]:
            consequences.append("would have won the matchup")
        if cd["optimal_beats_median"]:
            consequences.append("would have cleared the median")
        if consequences:
            cd_text = (
                f"{_fmt_team(cd['team'])} left {cd['points_left_on_bench']:.1f} points on the bench - "
                f"the optimal (fully legal) lineup {' and '.join(consequences)}."
            )
        else:
            cd_text = (
                f"{_fmt_team(cd['team'])} left {cd['points_left_on_bench']:.1f} points on the bench - "
                "didn't change the result either way."
            )
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
        f"{_fmt_team(gtw['home'])} ({gtw['home_projected_score']:.1f} proj) vs {_fmt_team(gtw['away'])} "
        f"({gtw['away_projected_score']:.1f} proj), assuming each team sets an optimal lineup "
        f"(home win probability: {gtw['home_win_probability']:.0%})" if gtw else "Schedule not available yet.",
    )

    return "\n".join(lines).strip()


def _build_claude_prompt(facts: dict) -> str:
    season, week = facts["season"], facts["week"]
    bad_beat_team_pk = ((facts["awards"].get("bad_beat") or {}).get("team") or {}).get("team_pk")
    bad_beat_instructions = (
        "For the BAD BEAT section specifically, you have a `web_search` tool - use it to look up REAL NFL "
        f"news from the actual {season} NFL season, Week {week} (in-game injuries, overturned/reviewed "
        "plays, garbage-time or kneel-down finishes, officiating controversies, or any other real 'bad "
        "beat' storyline from that week's real games). This section is ABOUT ONE SPECIFIC TEAM: "
        f"`awards.bad_beat.team` (team_pk {bad_beat_team_pk!r} - the highest scorer among that week's real "
        "losing teams, i.e. a team that scored well but still lost). Search only for news connecting to "
        "PLAYERS ON THAT TEAM'S OWN ROSTER (look them up in `starting_rosters` for that team_pk) - never "
        "cite a real news story about a player on a DIFFERENT team, even if it's a great story, and never "
        "add an 'honorable mention' about an unrelated team/player. The story must plausibly explain why "
        "this specific team's week went badly (an injury, a bad break, bad luck) - not just any player on "
        "their roster who happens to be in the news. Describe only what your search results actually "
        "support (never embellish beyond them). If nothing search-worthy connects to THIS team's own "
        "roster, skip the news angle and just report the plain fact from `awards.bad_beat` (they scored "
        "X and still lost) instead of forcing a connection that isn't real, or reaching for a different "
        "team's story. Do not narrate your search process (no \"I'll search for...\") - output only the "
        "final recap text below."
    )
    other_instructions = (
        "For MANAGER OF THE WEEK: this is about roster MANAGEMENT, not who scored the most or won by the "
        "most (that's BEATDOWN OF THE WEEK) - base it on `awards.best_lineup_efficiency` (how close to "
        "their own optimal possible lineup they actually got). If `awards.smart_lineup_call` is present, "
        "also mention it as a second highlight in this same section: a manager who started a player "
        "projected LOWER than a bench alternative eligible for that exact slot, and it paid off - a good "
        "read/gut call, not a fluke (the alternative really could have started there; this isn't an "
        "illegal position swap).\n\n"
        "For COACHING DISASTER: `awards.coaching_disaster.points_left_on_bench` already comes from a "
        "FULLY LEGAL optimal lineup (every swap respects real position eligibility - e.g. it would never "
        "sub a QB into a bench WR's slot), so don't hedge or caveat that it might be an illegal or "
        "impossible swap. Report both `flipped_result` (would the optimal lineup have won the real "
        "matchup) and `optimal_beats_median` (would it have also cleared that week's median/top-half "
        "bonus, if this league uses one) plainly, whichever combination applies.\n\n"
        "For NEXT WEEK'S GAME TO WATCH: `next_week_game_to_watch.home_projected_score`/"
        "`away_projected_score` already assume each team sets an OPTIMAL lineup from their current roster "
        "using next week's real ESPN projections (with a free-agent-average stand-in for any empty slot) "
        "- NOT each team's actual current lineup, since nobody has set next week's lineup yet. Present it "
        "that way (e.g. 'if both sides set their best lineup') - don't claim this IS either team's live "
        "current projection or win probability as of right now."
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
        + "\n\n" + other_instructions
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
    conn: sqlite3.Connection, season: int, week: int, force_regenerate: bool = False, log=print, league=None
) -> dict | None:
    """Returns {"facts": dict, "commentary": str, "source": str,
    "generated_at": str} or None if the week hasn't completed yet.
    Checks weekly_recaps first (unless force_regenerate) so repeat page
    views never re-trigger a Claude call - `league` (see build_weekly_
    facts) only matters on that first, cached-afterward generation."""
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

    facts = build_weekly_facts(conn, season, week, league=league)
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
