# Project Brief - Fantasy Football League Dashboard

This file is the source of truth for a new Claude Code session picking up
this project. Read this first, then check the "Current Status" section at
the bottom before doing anything else.

## Goal

Build a polished fantasy football league analytics dashboard for a private
ESPN Fantasy Football league (friend group). It should feel like a mix of
ESPN, The Athletic, and a private league group chat.

Primary objectives:
1. Genuinely useful fantasy analytics.
2. Surface funny statistical anomalies that fuel league trash talk.
3. Maintain historical league data across seasons.
4. Auto-update from ESPN (no CSV uploads).
5. All metrics computed deterministically in Python/pandas. Claude is used
   ONLY for written commentary, never for calculating stats.

## Tech stack (fixed, do not change)

- Python
- `espn-api` package for ESPN Fantasy Football data
- Streamlit for the web UI
- SQLite for persistent storage
- pandas for analysis, Plotly for the few charts that earn their place
- python-dotenv for credentials
- Anthropic API (optional) for weekly recap commentary only

Do not overengineer. This runs locally for a private friend group today,
but structure the code so it could later deploy to Streamlit Community
Cloud, Render, Railway, etc.

## Security

- Credentials (`LEAGUE_ID`, `ESPN_S2`, `SWID`) come from environment
  variables only, loaded via `.env` (gitignored). Never hardcode secrets.
- `.env.example` documents the required vars with no real values.
- `ANTHROPIC_API_KEY` is optional - the app must work without it
  (deterministic placeholder commentary as fallback).

## Architecture (keep these layers separate)

1. ESPN data ingestion (`fantasy_football/espn_client.py`, later
   `fantasy_football/ingest.py`)
2. SQLite persistence (`fantasy_football/db.py` + schema)
3. Metric calculations (`fantasy_football/metrics/`) - deterministic only
4. Dashboard presentation (`Home.py` + `pages/`) - Streamlit
5. Claude-generated commentary (`fantasy_football/commentary.py`) -
   consumes structured calculated results, never computes stats itself

## Full functional spec

### Data ingestion
Pull and normalize: league settings, managers, teams, rosters, weekly
matchups, weekly team scores, player scores, starting lineups, bench
players, final standings, draft picks, transactions, trades, waiver/FA
activity, and previous seasons. Don't over-query ESPN. SQLite is the
persistent warehouse. Refresh must be idempotent (no duplicate records on
re-run). Streamlit needs a "Refresh ESPN Data" button and must display the
last successful refresh timestamp.

### Database
Minimum normalized tables: `seasons`, `managers`, `teams`, `matchups`,
`weekly_team_scores`, `weekly_rosters`, `player_week_scores`,
`draft_picks`, `transactions`, `metrics_weekly`. Add more as needed
(e.g. `power_rankings_history`, `weekly_awards`, `weekly_recaps`).
Manager identity must persist across team name changes and across
seasons - use ESPN's stable member/team IDs (see `league.members`,
`team.owners`, `team.team_id`). Each manager should have one team per
season unless ESPN data says otherwise.

### Dashboard navigation
Pages: HOME, MATCHUPS, LUCK, MANAGERS, HISTORY, WEEKLY RECAP. Polished
modern sports-dashboard look (cards, typography, rankings, deltas, badges,
tabs, clean tables) - not a default Streamlit demo. Limited, purposeful
charts; prioritize information density and readability. Sidebar: league
name, current season, current week, last refresh timestamp, refresh
button, season switcher.

### HOME
Four headline cards: BEST TEAM, BIGGEST FRAUD, UNLUCKIEST TEAM, PROJECTED
CHAMPION. Standings + power rankings table with columns: Rank, Change vs
prior week, Team, Record, PPG, All-Play Win %, Power Score, Playoff
Probability, short commentary.

Power Score (0-100 normalized), weights:
- 35% Points Per Game percentile
- 30% All-Play Win %
- 20% Recent Form (last 3 completed games, or all available if <3)
- 15% Actual Win %

Store every week's power ranking snapshot (for rank-movement history).

### All-Play record
For every completed week, compute each team's record if it had played
every other team that week (e.g. 10-team league, 3rd highest score that
week = 7-2 all-play). Aggregate season-wide: All-Play W/L, All-Play Win %,
Expected Wins = All-Play Win % x Actual Games Played.

### Luck dashboard
Luck Wins = Actual Wins - Expected Wins (positive = lucky). Table columns:
Luck Rank, Team, Actual Record, Expected Record, Luck Wins, Points Against
Rank, All-Play Record. Also: highest score in a loss, lowest score in a
win, most/fewest points against, # of top-3 weekly scores that lost, # of
bottom-3 weekly scores that won.

Fraud Index = Actual Win % - All-Play Win % (higher = more fraudulent).
Badge thresholds (configurable): LEGIT, SLIGHTLY SUSPICIOUS, FRAUD WATCH,
GENERATIONAL FRAUD.

### Matchups page
Per-matchup cards for selected week: Team, Record, Power Rank, Season PPG,
Last 3 PPG, All-Play %, Luck Rank, historical head-to-head. For future
matchups, compute a custom win probability (clearly distinguished from
ESPN's own projection):
- Expected score = 60% season PPG + 40% last-3-week PPG
- Score distribution from each team's historical weekly scoring stdev
- Win probability via simulation or analytical method

### Manager efficiency (lineup)
Using weekly roster + player scores, compute the optimal legal starting
lineup per manager per week (respect real roster/slot settings pulled from
ESPN `league.settings.position_slot_counts` - never hardcode positions).
Actual Starter Points, Optimal Starter Points, Lineup Efficiency = Actual /
Optimal, Points Left on Bench. Determine if a different legal lineup would
have won the matchup. Track Manager-Caused Losses, Actual Record vs
Optimal-Lineup Record. Season manager efficiency rankings.

### History
Import as much prior-season ESPN history as available (`league.
previousSeasons`). Hall of Fame: championships, finals/playoff appearances,
career W/L/win%/points, highest/lowest scoring season per manager. League
records: highest/lowest weekly score, biggest blowout, closest game, most
points in a loss, lowest score in a win, highest season points, longest
win/loss streaks. Head-to-head rivalry explorer: pick Manager A/B, show
all-time series, total/avg points, playoff record, largest victory,
closest game, current streak, full matchup history. Manager identity must
persist even when team names change.

### Weekly awards (stored historically)
Manager of the Week, Highest/Lowest Score, Biggest Blowout, Closest Game,
Bad Beat, Luckiest Win, Unluckiest Loss, Coaching Disaster, Biggest Fraud,
Best/Worst Lineup Efficiency - computed automatically per completed week.

### Weekly recap
Build a structured JSON of the week's calculated facts. Commentary module
must work with a deterministic placeholder (no API key required) AND
support Anthropic-generated commentary when `ANTHROPIC_API_KEY` is set.
Save generated recaps in SQLite so they aren't regenerated unnecessarily.

Claude prompt requirements: sound like ESPN/The Athletic crossed with
friendly group-chat trash talk, be funny, base every joke ONLY on supplied
stats, never invent stats, keep it concise. Sections: HEADLINE, GAME OF THE
WEEK, BEATDOWN OF THE WEEK, BAD BEAT, MANAGER OF THE WEEK, COACHING
DISASTER, FRAUD WATCH, POWER RANKING MOVERS, NEXT WEEK'S GAME TO WATCH.

### Playoff simulation
Monte Carlo, >=10,000 sims. Per remaining matchup: Expected Score = 60%
season PPG + 40% last-3 PPG, using each team's observed weekly stdev for
simulated scores. Use league's actual playoff qualification/seeding
settings where obtainable (`league.settings`: `playoff_team_count`,
`playoff_seed_tie_rule`, division info, etc.). Output: Playoff %, Bye %,
#1 Seed %, Championship %. If championship-round settings are unsupported
by ESPN's API, implement playoff-qualification probability first and
document the limitation clearly - do not guess.

### Data quality
Validate on every refresh: completed matchups have exactly two teams;
weekly scores reconcile to matchup scores; all-play results have the
expected number of comparisons; one team per manager per season (unless
ESPN says otherwise); no duplicate matchup records. Log warnings. Never
fabricate missing data.

### Testing
Unit tests (small mock leagues, no live ESPN calls) for: all-play calc,
expected wins, luck score, power score normalization, optimal lineup calc,
head-to-head history calc.

## Implementation phases (build and test in this order)

1. Project structure, env config, ESPN auth, basic connection test
2. SQLite schema, current-season ingestion, historical-season ingestion,
   refresh workflow
3. Standings, All-Play, Luck Index, Fraud Index, Power Rankings
4. Streamlit Home, Luck dashboard, Matchups pages
5. Manager lineup efficiency, optimal lineup engine
6. Historical records, Hall of Fame, head-to-head rivalry explorer
7. Playoff probabilities (Monte Carlo)
8. Weekly recap framework + Claude integration

After each phase: run the app, test the functionality, fix errors before
moving on, update the README "What works" section. Don't leave large
chunks of untested placeholder code.

## Key espn-api facts already discovered (don't re-derive these)

- `League(league_id, year, espn_s2, swid)` - constructing it fetches
  everything eagerly.
- `league.previousSeasons` - list of prior season years ESPN reports as
  available (only populated after `_fetch_league`, i.e. after
  construction).
- `league.settings.position_slot_counts` - dict of roster slot -> count,
  pulled from ESPN, not hardcoded. Use this for the optimal-lineup engine.
- `league.settings.playoff_team_count`, `.reg_season_count`,
  `.division_map`, `.playoff_seed_tie_rule`, `.matchup_periods` - playoff
  and schedule structure.
- `league.members` - list of member dicts (stable manager identity).
  `team.owners` - list of member dicts owning that team (a team can have
  co-owners). Use member `id` as the stable manager key across seasons,
  NOT team_id (team_id can be reassigned/team names can change).
- `league.box_scores(week)` - list of `BoxScore` with `.home_lineup` /
  `.away_lineup` (list of `BoxPlayer`, each with `.slot_position`,
  `.points`, `.eligibleSlots` inherited from `Player`). This is what
  the optimal lineup engine needs. Only available for year >= 2019.
- `league.free_agents()`, `league.transactions()`,
  `league.recent_activity()` - waiver/trade data. `recent_activity()` and
  `box_scores()`/`free_agents()` require year >= 2019.
- `league.draft` - list of `BasePick` after construction (drafted picks).
- `league.power_rankings(week)` - ESPN's own two-step-dominance power
  rankings exist as a library method, but we're building our OWN power
  score per spec (35/30/20/15 weighting) - don't just use this method,
  though it's fine as a reference/sanity check.
- Constructing a `League` for a given year makes a live network call
  immediately (no lazy option) - so ingestion code should construct one
  `League` per season needed and reuse it, not construct repeatedly.

## Current status (update this section as phases complete)

**Repo:** `mattklei1/fantasy-football` on GitHub, `main` branch (pushed
directly so far, no PR yet - none was requested).

**Environment:** This is developed inside a Claude Code cloud sandbox.
ESPN's API host (`fantasy.espn.com` / `lm-api-reads.fantasy.espn.com`) is
NOT reachable by default - the sandbox's egress proxy blocks it unless the
environment's network policy (claude.ai/code -> environment settings ->
Capabilities -> domain allowlist) explicitly allows those two hosts. The
user has already added both to their environment's allowlist, but the
policy only applies to newly-created sessions/containers - if
`python test_connection.py` still 403s, it means this session predates
that change; nothing else is wrong.

**Real credentials exist** in the user's local `.env` (gitignored, not in
git history): `LEAGUE_ID=1025842`, real `ESPN_S2`/`SWID`, `CURRENT_SEASON
=2026`. Ask the user to re-supply them if a fresh clone doesn't have
`.env` (it's gitignored, so a fresh clone/session won't have it - the user
has these already so just ask them to paste values again or confirm the
file exists in this workspace).

**Phase 1 (DONE):** `fantasy_football/config.py` (env var loading),
`fantasy_football/espn_client.py` (ESPN client wrapper w/ per-season
caching), `test_connection.py` (prints league validation summary). Not
yet confirmed against a live connection due to the sandbox network
restriction above - this is the first thing to verify in a new session.

**Phase 2 (NOT STARTED):** SQLite schema + ingestion + refresh workflow.
This is the next task.

**Phases 3-8:** Not started.

**First actions for a new session:**
1. `cd` into the repo, run `python test_connection.py` (venv should exist
   at `venv/` - recreate with `python3 -m venv venv && venv/bin/pip
   install -r requirements.txt` if the sandbox is fresh and venv/ isn't
   present - note venv/ is not gitignored-safe to assume persists across
   sessions, check first). Confirm the .env file is present and has real
   values before running - if missing, ask the user for
   LEAGUE_ID/ESPN_S2/SWID again.
2. Once the connection prints a clean summary, proceed to Phase 2 (SQLite
   schema + ingestion) per the implementation order above.
