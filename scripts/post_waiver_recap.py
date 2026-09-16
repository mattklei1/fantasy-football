#!/usr/bin/env python3
"""GitHub Actions entry point: post the weekly waiver-wire recap to
GroupMe. Runs standalone against live ESPN + FantasyPros data - see
.github/workflows/waiver-recap.yml for the schedule and
fantasy_football/waiver_report.py for the actual logic.

Usage: python scripts/post_waiver_recap.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fantasy_football import config
from fantasy_football.espn_client import ESPNClient
from fantasy_football.groupme_client import send_long_message
from fantasy_football.schedule_guard import is_target_time_now
from fantasy_football.waiver_report import build_message, enrich_with_suggested_bids, fetch_week_claims

# Default assumption, NOT verified against this league's real waiver
# settings (espn_api doesn't expose a "waiver processing day" field) -
# ESPN commonly processes FAAB waivers Tuesday night/Wednesday early AM
# ET, so Wednesday late-morning Pacific gives claims time to settle.
# Confirm against this league's actual pattern and adjust the workflow's
# cron + --target-hour if it's off.
DEFAULT_TARGET_HOUR = 9


def main() -> int:
    if not is_target_time_now(DEFAULT_TARGET_HOUR, 0):
        print("Not the target time yet (DST-safety window) - skipping.")
        return 0

    bot_id = config.groupme_bot_id()
    if not bot_id:
        print("GROUPME_BOT_ID not set - skipping post.")
        return 0

    client = ESPNClient()
    season = client.credentials.current_season
    league = client.get_league(season)
    week = league.current_week

    claims = fetch_week_claims(league, scoring_period=week)
    if not claims:
        print(f"No waiver activity for week {week} - skipping post.")
        return 0

    fp_api_key = config.fantasypros_api_key()
    if fp_api_key:
        # Real live budget, not a hardcoded assumption - this league's
        # real budget has been $100 every season 2024-2026, not the
        # $200 this used to pass (see war_room_data.FAAB_BUDGET_TOTAL's
        # correction note - same bug, this call site was fixed alongside
        # it 2026-09-16).
        budget = float(league.settings.acquisition_budget or 100.0)
        enrich_with_suggested_bids(
            claims, league, fp_api_key, season, league.settings.position_slot_counts, budget=budget
        )

    message = build_message(claims, week)
    send_long_message(bot_id, message)
    print(f"Posted waiver recap for week {week}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
