"""Fetches Justin Boone's (Yahoo Fantasy, FantasyPros' most accurate
in-season ranker for 2025 - confirmed live, 2026-09-14) REAL published
weekly rankings for a specific list of players, via a Claude call with
the web_search tool.

Why this exists instead of an API call: FantasyPros' licensed API (the
only source this project scrapes-never-scrapes-only-uses-the-API from)
does not expose individual-expert rankings under this project's plan -
confirmed live by testing its `filters` parameter, which is silently
ignored (every request echoes back the same fixed 2-expert blend
regardless of what's passed). Justin Boone DOES publish his real
rankings as ordinary public articles on Yahoo Sports every week
(confirmed live: a full Week 1 2026 set existed across QB/RB/WR/FLEX/
TE/DEF/K), so Claude reads those directly via web_search - the same
"Claude never computes stats, only finds and reports real published
facts" pattern already used for the Weekly Recap's BAD BEAT section and
the ESPN/Yahoo waiver-target cross-check.

Scoped to a SPECIFIC list of named players (not "get his whole top
300") for two reasons: it's cheaper/more reliable than trying to parse
a full ranking table out of search snippets, and it keeps Claude's job
narrow and checkable - report what he said about THESE players, don't
invent or estimate for anyone else.
"""
from __future__ import annotations

import json
import re

CLAUDE_MODEL = "claude-opus-5"
MAX_TOKENS = 3072


def _build_prompt(players: list[dict], season: int, week: int) -> str:
    player_lines = "\n".join(f"- {p['player_name']} ({p['position']})" for p in players)
    return (
        f"Search the web for Justin Boone's REAL published fantasy football rankings for the "
        f"{season} NFL season, Week {week}, on Yahoo Sports (sports.yahoo.com). He publishes "
        "separate weekly articles per position - search for his QB rankings, and his FLEX "
        "rankings (his combined RB/WR/TE ranking, which is what you need for these skill-"
        "position players since ranks aren't comparable across positions otherwise) for this "
        f"week specifically.\n\nFor each of these players, find and report the rank Justin Boone "
        f"gave them THIS WEEK (his FLEX rank for RB/WR/TE players, his QB rank for QB players):\n"
        f"{player_lines}\n\n"
        "Respond with ONLY a JSON array, no other text before or after it: "
        '[{"player_name": "...", "rank": <int or null>, "rank_type": "FLEX" or "QB" or null}, ...] '
        "- one entry per player listed above, in the same order. Use null for rank/rank_type if "
        "you genuinely can't find Justin Boone's real published opinion on that player this week "
        "(e.g. he didn't rank them, or you can't find the article) - NEVER estimate, guess, or "
        "invent a rank. Only report a rank you actually found in his real published rankings."
    )


def fetch_boone_rankings(players: list[dict], season: int, week: int, api_key: str) -> dict[str, dict]:
    """players: [{"player_name", "position"}, ...] (no player_id needed
    here - callers match back by name, see build_boone_lineup_plan).
    Returns {player_name: {"rank": int | None, "rank_type": str | None}}.
    Raises on any failure - this is a real, load-bearing input to the
    lineup feature (unlike the waiver cross-check, which is a nice-to-
    have), so callers should surface a failure clearly rather than
    silently produce a plan with no real ranking data behind it."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": _build_prompt(players, season, week)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined the Boone rankings lookup: {response.stop_details}")
    text = "".join(block.text for block in response.content if block.type == "text")
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"No JSON array found in Claude's response: {text[:500]}")
    parsed = json.loads(match.group(0))
    return {
        p["player_name"]: {"rank": p.get("rank"), "rank_type": p.get("rank_type")}
        for p in parsed
        if isinstance(p, dict) and p.get("player_name")
    }
