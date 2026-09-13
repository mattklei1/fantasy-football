#!/usr/bin/env python3
"""Phase 2 CLI: run the ESPN -> SQLite refresh workflow.

Usage:
  python refresh_data.py               # refresh all seasons ESPN reports as available
  python refresh_data.py 2026          # refresh just one season
  python refresh_data.py 2026 2025     # refresh specific seasons
"""
import sys
import time

from fantasy_football import db
from fantasy_football.espn_client import ESPNClient
from fantasy_football.ingest import refresh_all


def main() -> int:
    seasons = [int(a) for a in sys.argv[1:]] or None

    client = ESPNClient()
    start = time.time()
    status = refresh_all(client=client, seasons=seasons)
    elapsed = time.time() - start

    print()
    print("=" * 50)
    print(f"REFRESH {status.upper()} in {elapsed:.1f}s")
    print("=" * 50)

    conn = db.get_connection()
    counts = {}
    for table in (
        "seasons", "managers", "teams", "matchups", "weekly_team_scores",
        "players", "weekly_rosters", "player_week_scores", "draft_picks",
        "transactions",
    ):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()

    for table, count in counts.items():
        print(f"{table:22s} {count}")

    return 0 if status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
