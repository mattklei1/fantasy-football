"""Builds the two GroupMe messages for the Lineup Optimizer's scheduled
alerts (Wednesday prep check, Sunday game-day alert) from the same
war_room_data.build_ideal_lineup() result the War Room UI tab uses.
Plain text, no Markdown - GroupMe doesn't render it (see groupme_client.py).
"""
from __future__ import annotations


def _format_changes(changes: list[dict]) -> list[str]:
    return [f"- {c['player_name']} ({c['position']}): {c['from_slot']} -> {c['to_slot']}" for c in changes]


def build_wednesday_message(result: dict, team_name: str) -> str:
    """Suggested lineup changes ahead of the week - no urgency framing,
    this is a heads-up, not an alert."""
    changes = result["changes"]
    week = result["week"]
    lines = [f"WEEK {week} LINEUP CHECK FOR {team_name.upper()}", ""]
    if not changes:
        lines.append("Your lineup already matches the weekly consensus - no changes suggested.")
    else:
        lines.append("Suggested changes (vs FantasyPros' weekly consensus):")
        lines.extend(_format_changes(changes))
        lines.append("")
        lines.append("Review in War Room -> Lineup Optimizer to submit.")
    return "\n".join(lines).strip()


def build_sunday_message(result: dict, team_name: str) -> str:
    """High-priority zero-projected-starter alert FIRST, non-optimal
    decisions SECOND - per the user's explicit ordering request."""
    zero_proj = result["zero_projected_starters"]
    changes = result["changes"]
    week = result["week"]
    lines = [f"WEEK {week} SUNDAY LINEUP ALERT FOR {team_name.upper()}", ""]

    if zero_proj:
        lines.append("HIGH PRIORITY - projected for 0 points and currently starting:")
        for z in zero_proj:
            lines.append(f"- {z['player_name']} ({z['projected']:.1f} pts projected)")
        lines.append("")
    else:
        lines.append("No starters projected for 0 points.")
        lines.append("")

    if changes:
        lines.append("NON-OPTIMAL STARTING DECISIONS (vs FantasyPros' weekly consensus):")
        lines.extend(_format_changes(changes))
        lines.append("")
    else:
        lines.append("Your lineup already matches the weekly consensus.")
        lines.append("")

    if zero_proj or changes:
        lines.append("Review in War Room -> Lineup Optimizer to fix before kickoff.")
    return "\n".join(lines).strip()
