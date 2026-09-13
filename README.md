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

## Scheduled GroupMe posts

Three recurring in-season messages, sent via a GroupMe Bot (not the app itself - these run on
GitHub Actions, independent of whether the Streamlit app is deployed or awake):

- **Early Slate Update** (~1:30pm Pacific Sundays): live scores so far, closest games, top scorers
- **Afternoon Slate Update** (~5:00pm Pacific Sundays): same, later in the day
- **Waiver Wire Report** (~9:00am Pacific Wednesdays, unverified guess at this league's actual
  waiver day - adjust if wrong): every contested claim with all real bids placed (not just the
  winner), and winning bids that looked like overpays against our own suggested-value estimate

**Setup:**
1. Create a Bot for your GroupMe group at [dev.groupme.com](https://dev.groupme.com/bots) - takes
   its `bot_id`, no other access.
2. Add these as **GitHub Actions repository secrets** (Settings → Secrets and variables → Actions),
   matching your `.env` values: `LEAGUE_ID`, `ESPN_S2`, `SWID`, `CURRENT_SEASON`, `GROUPME_BOT_ID`,
   and `FANTASYPROS_API_KEY` (optional - powers the waiver report's suggested bid values).
3. That's it - `.github/workflows/slate-updates.yml` and `waiver-recap.yml` pick up from there.
   Use each workflow's "Run workflow" button (Actions tab) to test on demand instead of waiting
   for the schedule.

No suggested-bid number exists anywhere to pull from (checked FantasyPros' full API, ESPN, and
Yahoo - none of them publish one) - `fantasy_football/metrics/waiver_value.py` computes our own,
normalized to this league's real $200 budget and real superflex slot counts, not borrowed
assumptions. Always labeled as a heuristic, never presented as fact.

## Deploying (Streamlit Community Cloud)

Vercel/Next.js-style hosts don't work for this app - Streamlit needs one persistent Python
process holding a live connection per user, not a short-lived serverless function, so use a host
built for that. **Streamlit Community Cloud** is the right (free) choice: purpose-built for
Streamlit, and it comes with private-app access control built in.

1. **Push this repo to GitHub** (already done if you're reading this from the repo).
2. **Deploy**: at [share.streamlit.io](https://share.streamlit.io), connect your GitHub account,
   pick this repo/branch, and set `Home.py` as the main file.
3. **Set secrets**: in the app's *Advanced settings → Secrets*, paste your real values in the same
   flat `KEY = "value"` format as `.env.example` (NOT nested under a `[section]` - root-level
   secrets are what Streamlit exposes as real environment variables, which is what this app's
   `config.py` reads via `os.getenv()`):
   ```toml
   LEAGUE_ID = "1025842"
   ESPN_S2 = "..."
   SWID = "{...}"
   CURRENT_SEASON = "2026"
   ANTHROPIC_API_KEY = "..."
   FANTASYPROS_API_KEY = "..."
   GEMINI_API_KEY = "..."
   APP_PASSWORD = "pick-something-simple"
   ```
   Only `LEAGUE_ID`/`ESPN_S2`/`SWID` are required - everything else is optional and that feature
   just shows a setup message without it.
4. **Make it private**: in the repo's visibility / the app's sharing settings, keep the app
   private and add each league member's email as a viewer (Share button → enter email → Invite).
   Only people you've explicitly added can open the URL at all - it won't be listed or searchable.
5. **Set `APP_PASSWORD`** (step 3, above) as a second layer on top of the viewer allowlist - cheap
   insurance, and lets you share access more casually (e.g. a GroupMe message) without adding
   every single person as a named Streamlit viewer.

**Two things specific to this host, both already handled in code:**
- **Secrets → env vars**: confirmed Streamlit Cloud auto-exposes root-level secrets as real
  environment variables, so `config.py` needed zero changes.
- **The local filesystem isn't reliably persistent** - a redeploy, and possibly the 12-hour idle
  sleep/wake cycle, can wipe `data/league.db`. The app detects an empty database on load and
  automatically rebuilds it from ESPN (the same thing the "Refresh ESPN Data" button does) before
  showing any page - the first load after a restart takes a few minutes, every load after that is
  normal speed until the next wipe. No action needed, just don't be alarmed by a slow first load.
