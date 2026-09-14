#!/usr/bin/env python3
"""GitHub Actions entry point: post a live mid-Sunday matchup update to
GroupMe. Runs standalone against live ESPN data - no dependency on the
deployed Streamlit app's (possibly-wiped) local database, since this
executes in a completely separate environment (see
.github/workflows/slate-updates.yml).

Usage: python scripts/post_slate_update.py --label "Early Slate Update"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import config
from fantasy_football.espn_client import ESPNClient
from fantasy_football.groupme_client import send_long_message
from fantasy_football.schedule_guard import is_target_time_now
from fantasy_football.slate_report import (
    build_matchup_snapshots,
    build_message,
    fetch_box_scores,
    median_cutline,
    top_individual_scores,
    worst_lineup_decision,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="e.g. 'Early Slate Update'")
    parser.add_argument("--target-hour", type=int, required=True, help="Target Pacific hour (24h)")
    parser.add_argument("--target-minute", type=int, default=0)
    args = parser.parse_args()

    # the workflow fires several times across a DST-safety window (see
    # .github/workflows/slate-updates.yml) - only the firing actually
    # closest to the real target Pacific time does anything
    if not is_target_time_now(args.target_hour, args.target_minute):
        print("Not the target time yet (DST-safety window) - skipping.")
        return 0

    bot_id = config.groupme_bot_id()
    if not bot_id:
        print("GROUPME_BOT_ID not set - skipping post.")
        return 0

    client = ESPNClient()
    league = client.get_league(client.credentials.current_season)
    week = league.current_week

    box_scores = fetch_box_scores(league, week)
    matchups = build_matchup_snapshots(box_scores)
    if not matchups:
        # off-season, bye week, or nothing has kicked off yet - skip
        # silently rather than post a "nothing happening" message every
        # single Sunday year-round (the whole point is not spamming).
        print(f"No in-progress matchups for week {week} - skipping post.")
        return 0

    tops = top_individual_scores(box_scores, limit=3)
    worst_decision = worst_lineup_decision(box_scores, league.settings.position_slot_counts)
    cutline = median_cutline(league, box_scores)
    message = build_message(args.label, matchups, tops, worst_decision=worst_decision, cutline=cutline)
    send_long_message(bot_id, message)
    print(f"Posted: {args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
