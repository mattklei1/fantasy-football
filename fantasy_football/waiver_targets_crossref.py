"""Cross-references our own waiver suggestions against ESPN's and
Yahoo's own published weekly "top waiver wire targets" content, via a
Claude call with the web_search tool. "Claude never computes stats"
extends here too: Claude's only job is finding and naming real players
ESPN/Yahoo actually highlighted that week - matching those names
against our own REAL live free-agent board (so we never claim a
rostered player is "available" just because a search result named them)
and deciding what's actually worth reporting is 100% deterministic
Python, not Claude guessing at this league's own roster state.
"""
from __future__ import annotations

import json
import re

import pandas as pd

CLAUDE_MODEL = "claude-opus-5"
MAX_TOKENS = 2048


def _build_prompt(season: int, week: int) -> str:
    return (
        f"Search the web for ESPN's and Yahoo's own published fantasy football waiver "
        f"wire pickup/target articles for the {season} NFL season, Week {week} (try "
        f"searches like 'ESPN fantasy football waiver wire week {week}' and 'Yahoo "
        f"fantasy football waiver wire targets week {week}'). List the real player names "
        "each site names as a top waiver-wire target that week - nothing else.\n\n"
        "Respond with ONLY a JSON array, no other text before or after it: "
        '[{"player_name": "...", "position": "...", "source": "ESPN"}, ...] '
        '(source is "ESPN" or "Yahoo"). Only include a player if you found a real '
        "source actually naming them as a waiver target that week - never invent a "
        "player or guess at one. If you can't find either site's article, return []."
    )


def fetch_espn_yahoo_targets(season: int, week: int, api_key: str) -> list[dict]:
    """Raises on any failure - this is a nice-to-have cross-check, never
    something that should block sending our own real recommendations
    (callers should catch broadly, same as generate_claude_commentary's
    callers do)."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
        messages=[{"role": "user", "content": _build_prompt(season, week)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined the waiver cross-check: {response.stop_details}")
    text = "".join(block.text for block in response.content if block.type == "text")
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return [p for p in parsed if isinstance(p, dict) and p.get("player_name")]


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def find_overlooked_targets(
    espn_yahoo_targets: list[dict], free_agent_board: pd.DataFrame, already_suggested_player_ids: set[int],
) -> list[dict]:
    """Deterministic matching, no Claude involved: for each name ESPN/
    Yahoo called out, look it up in OUR OWN real live free-agent board
    (case/punctuation-insensitive name match) and keep it only if it's a
    genuine match there (so we never surface a player who isn't actually
    available in THIS league) AND it isn't already one of our own top
    suggestions. Returns [{player_id, player_name, position, source,
    suggested_bid}], in the order first encountered."""
    if free_agent_board.empty or not espn_yahoo_targets:
        return []
    board = free_agent_board.copy()
    board["_norm_name"] = board["player_name"].apply(_normalize_name)

    overlooked = []
    seen_ids: set[int] = set()
    for target in espn_yahoo_targets:
        norm = _normalize_name(target.get("player_name", ""))
        if not norm:
            continue
        matches = board[board["_norm_name"] == norm]
        if matches.empty:
            continue
        row = matches.iloc[0]
        player_id = int(row["player_id"])
        if player_id in already_suggested_player_ids or player_id in seen_ids:
            continue
        seen_ids.add(player_id)
        overlooked.append(
            {
                "player_id": player_id,
                "player_name": row["player_name"],
                "position": row["position"],
                "source": target.get("source") or "?",
                "suggested_bid": row.get("suggested_bid"),
            }
        )
    return overlooked
