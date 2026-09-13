# Fantasy Football League Dashboard

A private analytics dashboard for our ESPN Fantasy Football league. Think
ESPN + The Athletic + group chat trash talk, backed by deterministic Python
stats and optional Claude-generated commentary.

## Stack

- Python, [espn-api](https://github.com/cwendt94/espn-api) for ESPN data
- Streamlit for the UI
- SQLite for persistent league history
- pandas / numpy for metric calculations
- Plotly for the handful of charts that earn their place
- Anthropic API (optional) for weekly recap commentary

## Setup

1. Create a virtualenv and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```

   - `LEAGUE_ID`: the numeric ID in your league's ESPN URL
     (`fantasy.espn.com/football/league?leagueId=XXXXXXX`).
   - `ESPN_S2` and `SWID`: cookies from your browser session while logged
     into fantasy.espn.com with access to this private league. In Chrome:
     DevTools → Application → Cookies → `https://fantasy.espn.com`, then
     copy the `espn_s2` and `SWID` values (SWID includes the curly braces).
   - `ANTHROPIC_API_KEY` is optional. Without it, weekly recaps use
     deterministic placeholder commentary instead of Claude-generated text.

   `.env` is gitignored - never commit real credentials.

3. Verify the ESPN connection:
   ```bash
   python test_connection.py
   ```
   This prints league name, season, current week, team count/names, and
   which previous seasons ESPN reports as available.

4. Pull data from ESPN into the local database (idempotent - safe to
   re-run any time):
   ```bash
   python refresh_data.py          # all seasons ESPN reports as available
   python refresh_data.py 2026     # just one season
   ```

5. Run the dashboard:
   ```bash
   streamlit run Home.py
   ```

## What works today

**Phase 1 - Project scaffold & ESPN connection (done)**
- Environment-variable credential loading (`fantasy_football/config.py`)
- Reusable ESPN client wrapper (`fantasy_football/espn_client.py`)
- `test_connection.py` validates credentials and prints a league summary

**Phase 2 - SQLite persistence & ingestion (done)**
- Full schema in `fantasy_football/db.py` (seasons, managers, teams,
  matchups, weekly scores, rosters, player scores, draft picks,
  transactions, refresh log)
- `fantasy_football/ingest.py` pulls everything from ESPN idempotently
- `refresh_data.py` CLI to run it

**Phase 3 - Metrics (done)**
- `fantasy_football/metrics/` computes All-Play record, Luck Wins, Fraud
  Index, and Power Score per team per week (regular season only)
- Handles this league's rule history: PPR/roster/flex changes across
  seasons, and the top-half "median" bonus win format added in 2025
- Unit tested with mock data (`tests/test_metrics.py`) - no live ESPN
  calls needed to run the test suite

**Phase 4 - Dashboard: Home, Matchups, Luck (done)**
- `Home.py`: headline cards (Best Team, Biggest Fraud, Unluckiest Team)
  + full standings/power rankings table with week-over-week rank change
- `pages/1_Matchups.py`: per-week matchup cards with a custom (non-ESPN)
  win probability for games that haven't been played yet
- `pages/2_Luck.py`: Luck/Fraud table, matchup-vs-median record
  breakdown, and notable stat callouts (highest score in a loss, etc.)
- Sidebar: season switcher, week selector, last-refresh timestamp, and a
  "Refresh ESPN Data" button

**Roster Strength (done)**
- `pages/3_Roster_Strength.py`: forward-looking roster quality (not past
  performance) blending this week's ESPN projection with ESPN's
  season-long positional rank, with a starter/bench weighting that shifts
  across the season as bye weeks come and go
- Only reflects the current week of the current season - see the page's
  own methodology expander for details and limitations

Later phases (lineup efficiency, History/Hall of Fame, playoff
simulation, Claude commentary) will be documented here as they land.
