"""Builds the GroupMe-ready message for the Tuesday waiver-recommendations
notification - the same top-N suggestions War Room's My Waiver Bids tab
computes (war_room_data.get_my_waiver_suggestions), plus an optional
"also worth a look" section for real ESPN/Yahoo waiver targets our own
list missed (waiver_targets_crossref.find_overlooked_targets). Plain
text, no Markdown - GroupMe doesn't render it (see groupme_client.py).
"""
from __future__ import annotations


def build_message(
    suggestions: list[dict], overlooked: list[dict], week: int, budget_remaining: float, team_name: str
) -> str:
    if not suggestions and not overlooked:
        return f"WEEK {week} WAIVER RECOMMENDATIONS\n\nNo real free-agent upgrades found for {team_name} this week."

    lines = [
        f"WEEK {week} WAIVER RECOMMENDATIONS FOR {team_name.upper()}",
        f"Budget remaining: ${budget_remaining:.0f}",
        "",
    ]

    if suggestions:
        lines.append("TOP PICKS:")
        for i, s in enumerate(suggestions, start=1):
            pro_team = f", {s['pro_team']}" if s.get("pro_team") else ""
            lines.append(f"{i}. {s['player_name']} ({s['position']}{pro_team}) - suggested ${s['suggested_bid']:.0f}")
            lines.append(f"   {s['reasoning']}")
            if s.get("suggested_drop"):
                lines.append(f"   Drop: {s['suggested_drop']}")
        lines.append("")

    if overlooked:
        lines.append("ALSO WORTH A LOOK (ESPN/Yahoo top targets not already on our list):")
        for o in overlooked:
            bid = f" - suggested ${o['suggested_bid']:.0f}" if o.get("suggested_bid") is not None else ""
            lines.append(f"- {o['player_name']} ({o['position']}) - via {o['source']}{bid}")
        lines.append("")

    lines.append("Review in War Room -> My Waiver Bids to submit before claims lock.")
    return "\n".join(lines).strip()
