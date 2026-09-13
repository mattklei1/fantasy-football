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

4. Run the dashboard:
   ```bash
   streamlit run Home.py
   ```

## What works today

**Phase 1 - Project scaffold & ESPN connection (done)**
- Environment-variable credential loading (`fantasy_football/config.py`)
- Reusable ESPN client wrapper (`fantasy_football/espn_client.py`)
- `test_connection.py` validates credentials and prints a league summary

Later phases (SQLite persistence, metrics, dashboard pages, playoff
simulation, Claude commentary) will be documented here as they land.
