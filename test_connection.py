#!/usr/bin/env python3
"""Phase 1 sanity check: verify ESPN credentials work and print a clean
validation summary. Run with: python test_connection.py
"""
import sys

from fantasy_football.config import ConfigError, load_espn_credentials
from fantasy_football.espn_client import ESPNClient


def main() -> int:
    try:
        creds = load_espn_credentials()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    print(f"Connecting to ESPN league {creds.league_id}, season {creds.current_season}...")

    try:
        client = ESPNClient(creds)
        summary = client.summary()
    except Exception as exc:  # noqa: BLE001 - top-level CLI error surface
        print(f"Failed to connect to ESPN: {exc}")
        print(
            "Double-check LEAGUE_ID, ESPN_S2, and SWID in your .env file. "
            "ESPN_S2/SWID come from your browser cookies while logged into "
            "fantasy.espn.com with access to this private league."
        )
        return 1

    print()
    print("=" * 50)
    print("ESPN CONNECTION VALIDATED")
    print("=" * 50)
    print(f"League Name:      {summary.league_name}")
    print(f"League ID:        {summary.league_id}")
    print(f"Season:           {summary.season}")
    print(f"Current Week:     {summary.current_week}")
    print(f"Number of Teams:  {summary.team_count}")
    print("Team Names:")
    for name in summary.team_names:
        print(f"  - {name}")
    if summary.previous_seasons:
        print(f"Previous Seasons Available: {sorted(summary.previous_seasons)}")
    else:
        print("Previous Seasons Available: none reported by ESPN")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    sys.exit(main())
