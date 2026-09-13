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
   - `FANTASYPROS_API_KEY` is optional (a licensed key - see Roster Strength above).
   - `GEMINI_API_KEY` is optional. Without it, the Ask Me Anything page shows a setup message
     instead of crashing.

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
  performance) blending this week's ESPN projection, ESPN's season-long
  positional rank, and FantasyPros' rest-of-season expert consensus rank
  (weighted 20:15:40 - FantasyPros is the largest signal since it's the
  only genuinely forward-looking one), with a starter/bench weighting
  that shifts across the season as bye weeks come and go
- FantasyPros integration requires a licensed `FANTASYPROS_API_KEY` (see
  `.env.example`) - without one, the app falls back to the ESPN-only 2
  signals rather than fabricating the third
- Only reflects the current week of the current season - see the page's
  own methodology expander for details and limitations

**Lineup Efficiency (done)**
- `pages/4_Lineup_Efficiency.py`: Actual vs. Optimal starter points using
  an exact optimal-lineup solver (scipy Hungarian algorithm, not a
  greedy heuristic), lineup efficiency %, points left on bench, an
  optimal-lineup win/loss record, and manager-caused-loss counts
- **Decision Accuracy**: a points-blind companion metric - compares the
  set of players you actually started against the set the optimal
  lineup would have started, so one wrong start/sit swap always counts
  as one wrong decision, regardless of how many points a boom/bust
  bench player was worth (which otherwise dominates the points-based
  efficiency number)
- **Scope filter**: Regular Season / Playoffs / All. Playoffs means true
  championship-bracket weeks only - consolation-ladder games and playoff
  bye weeks are excluded, since they were never a shot at the title. A
  team with fewer playoff appearances naturally contributes fewer weeks
  to its own numerator/denominator under "All" - that's intentional, not
  a bug (fewer shots at it).
- Available for 2019+ seasons

**Playoff Odds (done)**
- `pages/6_Playoff_Odds.py`: Monte Carlo simulation (10,000 trials) of every remaining
  regular-season game plus the full playoff bracket - Playoff %, Bye %, #1 Seed %, and
  Championship % per team
- Each simulated game's score is drawn from the same model as the Matchups page's win
  probability (`60% season PPG + 40% last-3-week PPG`, Normal distribution using each team's
  own scoring stdev); seeding and the playoff bracket shape (fixed, not reseeded - confirmed
  against 10 real completed seasons) exactly match this league's real ESPN settings
- Validated against the real, fully-completed 2025 season: reproduced the exact real playoff
  field, bye teams, and #1 seed (all deterministic once the season is over), and gave the real
  champion a strong, plausible title probability rather than a degenerate result
- Requires at least 1 completed regular-season week this season to project from

**History (done)**
- `pages/5_History.py`: Hall of Fame (championships, finals/playoff
  appearances, career record, best/worst season by era-normalized PPG
  percentile), League Records (highest/lowest score, biggest blowout,
  closest game, etc. - raw record-book facts spanning every season), and
  a Head-to-Head rivalry explorer (pick any two managers, see their
  all-time series, points, playoff record, current streak, and full
  matchup history)
- Manager identity persists across team name changes AND across ESPN
  account id changes (a real thing that happened for 2 managers in
  2026 - see `db.primary_owner_join_sql`)

**Ask Me Anything (done)**
- `pages/8_Ask_Me_Anything.py`: a natural-language search bar over league history, standings,
  rosters, and past weeks, powered by Gemini Flash
- FantasyPros rankings are STRUCTURALLY unreachable, not just prompted against - Gemini's
  generated SQL runs through 3 independent layers (text validation, a genuinely read-only SQLite
  connection, and SQLite's own `set_authorizer` access control) that deny the
  `fantasypros_rankings` table outright. No credentials (FantasyPros/GroupMe/etc.) are ever at
  risk either way - they live only in `.env`, never in the database
- No login required - pick your name from an "Ask as" dropdown so "my roster"-style questions
  resolve, same trust model as the rest of the app
- Requires `GEMINI_API_KEY` in `.env` (get one at https://aistudio.google.com/apikey) - without
  it, the page shows a setup message rather than crashing
- A simple 20-questions-per-hour session rate limit keeps API spend bounded

**Weekly Recap (done)**
- `pages/7_Weekly_Recap.py`: a recap of the selected week, built ONLY from stats already
  calculated elsewhere - Claude (or a deterministic placeholder, when `ANTHROPIC_API_KEY` isn't
  set) never computes a single number, only the writeup
- Sections: Headline, Game of the Week, Beatdown of the Week, Bad Beat, Manager of the Week,
  Coaching Disaster, Fraud Watch, Power Ranking Movers, Next Week's Game to Watch
- Generated once per season/week and stored (`weekly_recaps` table) - a page view never
  re-triggers a Claude call; use the "Regenerate" button to force a fresh one
- An "Underlying facts" expander shows the exact structured JSON the recap was written from
- Requires a completed regular-season week to recap - no partial-week recaps
