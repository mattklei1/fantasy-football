"""Builds the two GroupMe messages for the Lineup Optimizer's scheduled
alerts (Wednesday prep check, Sunday game-day alert) from the same
war_room_data.build_ideal_lineup() result the War Room UI tab uses.
Marks up key phrases with **bold** and a couple of section-header emoji
- groupme_client.send_long_message converts the markup to real Unicode
bold before posting (GroupMe itself has no native formatting).
"""
from __future__ import annotations


def _format_changes(changes: list[dict]) -> list[str]:
    lines = []
    for c in changes:
        swap_note = f" (swaps with {c['swap_with_player_name']})" if c.get("swap_with_player_name") else ""
        lines.append(f"- {c['player_name']} ({c['position']}): {c['from_slot']} -> {c['to_slot']}{swap_note}")
    return lines


def _league_prefix(league_name: str | None) -> str:
    # league_name identifies which league this personal-bot message is
    # for, kept to one short line - this bot serves multiple leagues.
    return f"🏆 {league_name}\n\n" if league_name else ""


def build_wednesday_message(result: dict, team_name: str, league_name: str | None = None) -> str:
    """Suggested lineup changes ahead of the week - no urgency framing,
    this is a heads-up, not an alert."""
    changes = result["changes"]
    week = result["week"]
    lines = [f"{_league_prefix(league_name)}📋 **WEEK {week} LINEUP CHECK FOR {team_name.upper()}**", ""]
    if not changes:
        lines.append("✅ Your lineup already matches the weekly consensus - no changes suggested.")
    else:
        lines.append("**Suggested changes** (vs FantasyPros' weekly consensus):")
        lines.extend(_format_changes(changes))
        lines.append("")
        lines.append("Review in War Room -> Lineup Optimizer to submit.")
    return "\n".join(lines).strip()


def build_sunday_message(result: dict, team_name: str, league_name: str | None = None) -> str:
    """High-priority zero-projected-starter alert FIRST, non-optimal
    decisions SECOND - per the user's explicit ordering request."""
    zero_proj = result["zero_projected_starters"]
    changes = result["changes"]
    week = result["week"]
    lines = [f"{_league_prefix(league_name)}🏈 **WEEK {week} SUNDAY LINEUP ALERT FOR {team_name.upper()}**", ""]

    if zero_proj:
        lines.append("🚨 **HIGH PRIORITY** - projected for 0 points and currently starting:")
        for z in zero_proj:
            lines.append(f"- {z['player_name']} ({z['projected']:.1f} pts projected)")
        lines.append("")
    else:
        lines.append("✅ No starters projected for 0 points.")
        lines.append("")

    if changes:
        lines.append("⚠️ **NON-OPTIMAL STARTING DECISIONS** (vs FantasyPros' weekly consensus):")
        lines.extend(_format_changes(changes))
        lines.append("")
    else:
        lines.append("Your lineup already matches the weekly consensus.")
        lines.append("")

    if zero_proj or changes:
        lines.append("Review in War Room -> Lineup Optimizer to fix before kickoff.")
    return "\n".join(lines).strip()
