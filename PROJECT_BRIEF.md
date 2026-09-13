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

**Phase 2 schema (DONE, see `fantasy_football/db.py`):** implemented all
the minimum tables above plus `team_owners` (many-to-many join for
co-owned teams, and the mechanism for stable manager identity - joins on
`managers.manager_id`, ESPN's member GUID) and `refresh_log`. `players`
is a shared dimension table (not per-team) since a real NFL player's
score for a week is a single fact; `weekly_rosters` (team+week+player+
slot+starter) and `player_week_scores` (player+week+points, team-agnostic)
are deliberately split so the same player's real score isn't duplicated
if ownership logic ever needs revisiting. `metrics_weekly` schema exists
but is NOT populated yet - that's Phase 3. `seasons` also carries
`scoring_type`, `median_scoring`, `reception_points`,
`position_slot_counts` (JSON), `scoring_format_json` (JSON) to support
the cross-season normalization and median-win requirements below - these
were added specifically because this league's rules have changed across
seasons (see the two callouts in the Luck dashboard and History sections
below).

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

**IMPORTANT - median/top-half bonus win format (user-specified 2026-09-13):**
Since the 2025 season this league uses ESPN's native top-half scoring
bonus (`league.settings.median_scoring` / `scoringEnhancementType ==
'WIN_BONUS_TOP_HALF'`, confirmed True for 2025 and 2026, False for
2015-2024 in this league's history). Under this format each team earns
TWO win/loss results per week:
1. **Matchup record** - the real head-to-head result vs that week's
   scheduled opponent (derivable from `matchups`: home_score vs
   away_score).
2. **Median record** - whether the team's score that week was in the
   top half or bottom half of all teams' scores league-wide (derivable
   from `weekly_team_scores`: compare each team's score to that week's
   median across all teams). This is NOT the same thing as All-Play
   record above (All-Play compares against every single opponent
   individually; median record is a single top-half/bottom-half cut).
ESPN's official displayed win total for 2025+ seasons = matchup wins +
median wins combined. Luck/Fraud/Expected-Wins calculations for
2025+ seasons must decompose and expose BOTH components separately
(e.g. "Matchup Record", "Median Record", "Combined/Actual Record") -
don't just silently sum them into one opaque "wins" number, since the
whole point of the luck dashboard is to show which half of the record is
schedule luck vs which half is scoring-vs-field luck. For 2015-2024
seasons (median_scoring=False), actual wins = matchup wins only, no
median component. Nothing extra needs to be ingested for this - it's
fully computable from data already in `weekly_team_scores`/`matchups`;
this is Phase 3 (metrics) work, not ingestion.

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

**IMPORTANT - cross-season rule drift, so scoring is NOT directly comparable
across seasons (user-specified 2026-09-13):** this league has changed
scoring/roster rules multiple times. Confirmed via live data: 2015 was
standard scoring (no points-per-reception item at all); 2020/2025/2026
are half-PPR (0.5/reception). Roster composition changed too: 2015/2020
had no superflex/`OP` slot and 1 flex (`RB/WR/TE`) spot; 2025/2026 added
an `OP` (superflex-style) slot and a 2nd flex spot. `seasons.
reception_points`, `.position_slot_counts` (JSON), and `.scoring_format_json`
now capture this per season specifically so it isn't re-derived from
scratch later. Rule for Phase 3+: raw `points_for`/`points_against`/
weekly scores must NOT be compared directly across seasons with different
settings (e.g. "highest-scoring season ever" is meaningless if one season
was 0-PPR 1-flex and another was 0.5-PPR 2-flex+superflex). Any
cross-season comparison (Hall of Fame records, career point totals,
"best season ever") must normalize first - percentile rank or z-score of
each team-week/team-season relative to that season's own field, not raw
points. Same-season comparisons (current standings, this season's power
rankings, this season's luck) are unaffected and can keep using raw
points, since rules are constant within a season.

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
  `box_scores()`/`free_agents()` require year >= 2019. Empirically
  confirmed (2026-09-13): `recent_activity()` ALSO only works for the
  current season regardless of year>=2019 - calling it against a `League`
  constructed for a past year raises `ESPNInvalidLeague` ("League ...
  does not exist"). It's also only a rolling recent window, not full
  season history. So waiver/add/drop/trade history can only be captured
  going forward from whenever ingestion first runs each week - there is
  no way to backfill historical transactions for past seasons via this
  API. Document this as a known limitation, don't try to work around it.
- `league.box_scores(week)` returns BOTH matchup scores (home/away team +
  score + is_playoff) AND full player-level lineup data in one call - use
  it for both `matchups`/`weekly_team_scores` AND `weekly_rosters`/
  `player_week_scores` ingestion for year>=2019 (one call per week covers
  everything, don't also call `scoreboard()` for the same week - that
  would be a redundant, wasted ESPN call). `league.scoreboard(week)`
  actually re-fetches the ENTIRE season's schedule internally on every
  call and just filters client-side to the requested week - so it's only
  used as a fallback for year<2019 (no box_scores available), one call
  per week, accepted as an unavoidable inefficiency for those 4 seasons.
- Bounding which weeks to ingest: use `range(1, league.current_week + 1)`
  uniformly for every season. For a past (completed) season,
  `current_week` reports the season's final week, so this naturally
  covers the whole season. For the current season, it naturally stops at
  the in-progress week without pulling nonsense future-week data (calling
  `box_scores()`/`scoreboard()` for a week beyond `current_week` silently
  returns mislabeled current-week data instead of erroring, so don't do
  that).
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
UPDATE (2026-09-13): network egress to ESPN is confirmed working from this
sandbox - `lm-api-reads.fantasy.espn.com` is reachable and returns real
JSON responses (not a proxy block). The earlier note below about the
egress proxy blocking ESPN was correct for a prior session but is no
longer the blocker; don't assume network is the problem without checking
first (a raw `requests.get(...)` to the league endpoint returning a JSON
body, even a 401, proves the network path is fine).

Old note (kept for history): ESPN's API host (`fantasy.espn.com` /
`lm-api-reads.fantasy.espn.com`) was NOT reachable by default in an
earlier session - the sandbox's egress proxy blocked it unless the
environment's network policy (claude.ai/code -> environment settings ->
Capabilities -> domain allowlist) explicitly allowed those two hosts.

**Real credentials exist** in the user's local `.env` (gitignored, not in
git history): `LEAGUE_ID=1025842`, `CURRENT_SEASON=2026`. Ask the user to
re-supply `ESPN_S2`/`SWID` if a fresh clone doesn't have `.env` (it's
gitignored, so a fresh clone/session won't have it).

**RESOLVED (2026-09-13):** the first cookie pair the user pasted was stale
and got HTTP 401 `AUTH_LEAGUE_NOT_VISIBLE` from ESPN across 2024-2026 (and
even with no cookies at all, which is why the error alone wasn't
conclusive). Root cause: the user pasted the browser DevTools cookie value
still **URL-encoded** (`%2F`, `%2B`, `%3D` in place of `/`, `+`, `=`).
ESPN's DevTools Application > Cookies panel has a "Show URL-decoded"
toggle - the raw/default view is URL-encoded, and `espn_s2` must be
stored in `.env` in its **decoded** form (the one with literal `/`, `+`,
`=` characters) for `espn-api`/`requests` to send the correct Cookie
header. Always use the decoded value going forward.

**Phase 1 (DONE) - LIVE CONNECTION CONFIRMED 2026-09-13.**
`fantasy_football/config.py` (env var loading), `fantasy_football/
espn_client.py` (ESPN client wrapper w/ per-season caching),
`test_connection.py` (prints league validation summary) all working
end-to-end against the real league:
- League Name: "Salted by Quincy", League ID 1025842, Season 2026
- 12 teams, current week 1 (season not yet underway / week 1 in progress)
- Previous seasons available via ESPN: 2015-2025 (11 prior seasons) -
  this is a lot of history to backfill in Phase 2's historical ingestion.

**Phase 2 (DONE 2026-09-13):** SQLite schema + ingestion + refresh
workflow. New files: `fantasy_football/db.py` (schema + generic
idempotent `upsert()` helper), `fantasy_football/ingest.py` (all ESPN ->
SQLite ingestion functions + `refresh_all()`), `refresh_data.py` (CLI:
`python refresh_data.py [season ...]`, defaults to all available seasons).
Validated against the live league:
- Current season (2026, week 1 in progress) ingested and re-run confirmed
  idempotent (identical row counts on 2nd run, no duplicates).
- Spot-checked correctness: team/manager join, matchup scores, weekly
  roster + player score join, draft pick 1, transactions - all correct
  against what ESPN's own UI would show.
- Historical backfill (2015-2025) completed - all 12 seasons (2015-2026)
  now in `data/league.db`, took ~4 minutes total (box_scores requires one
  ESPN call per week per season for 2019+, scoreboard() fallback for
  2015-2018).
- **Bug found + fixed during backfill:** the `scoreboard()` fallback path
  (`ingest_week_scoreboard`, used for 2015-2018 only) crashed on any bye
  week ("`'Matchup' object has no attribute 'away_team'`") because
  `espn_api`'s `Matchup.home_team`/`.away_team` are bare type-hints with
  no default value when a side has no scheduled opponent - accessing them
  raises `AttributeError` instead of returning `None`. This silently
  dropped the ENTIRE week's matchup data (not just the bye team) for
  2015-2018 week 14 on the first backfill run. Fixed with `getattr(m,
  'home_team', None)` and now records a `weekly_team_scores` row for
  whichever side(s) are real teams even on a bye, while still only
  writing a `matchups` row when both sides are real. Re-ran the 4
  affected seasons after the fix; matchup count went 1061->1081 and
  weekly_team_scores 2122->2170, confirming the previously-lost week 14
  data is now captured. **Lesson for future ingestion work on this repo:
  wrap per-item logic in try/except or use `getattr`, don't let one bad
  record in a week silently take out the whole week** - the original bug
  only surfaced because of the `[warn]` log line, not a hard failure, so
  watch those logs on every backfill/refresh run.
- **Full 12-season data-quality validation (2026-09-13), all passed:**
  12/12 seasons present; 12 teams every season; zero duplicate matchup
  rows; zero matchups with home_team_pk == away_team_pk; zero completed
  weekly_team_scores rows with a NULL score; zero mismatches between
  `matchups.home_score/away_score` and the corresponding
  `weekly_team_scores` rows (full reconciliation join); draft pick counts
  per season are consistent with roster-size changes (192 = 12 teams x 16
  rounds for the older, smaller-roster seasons; 204 = 12 x 17 for the
  larger-roster/superflex seasons). Fun fact surfaced by the data: the
  league was named "USC Pike" through 2021 and became "Salted by Quincy"
  starting 2022 - worth keeping in mind for History-page team name vs.
  identity handling (this is a LEAGUE rename, not a team rename, so it
  doesn't affect the manager-identity design).
- Known limitation accepted as documented above: no historical
  transaction backfill possible (ESPN API constraint, not our bug).

**Phase 3 (DONE 2026-09-13):** Standings, All-Play, Luck Index, Fraud
Index, Power Rankings. New: `fantasy_football/metrics/` package
(`loaders.py` - pandas DataFrames out of SQLite; `season_metrics.py` -
pure, DB-free calculation functions, this is what's unit tested;
`pipeline.py` - orchestrates + upserts into `metrics_weekly`), plus
`tests/test_metrics.py` (9 tests, all passing, no live ESPN calls per the
spec's testing requirement). `refresh_data.py` now runs ingestion AND
recomputes metrics for every season in one command.

**Critical design decision made during this phase - metrics are
REGULAR-SEASON-ONLY:** All-Play/Luck/Fraud/Power Score are computed over
regular-season weeks only (`is_playoff = 0`), not the full season
including playoffs. This was discovered, not assumed: reconciling my
computed matchup+median record and points_for against ESPN's own
`team.wins`/`.losses`/`.points_for` for a full season showed an EXACT
match through the last regular-season week (2025 week 14) but diverged
once playoff weeks were included - ESPN's own team totals simply stop
accumulating at the end of the regular season. Also, the playoff bracket
shrinks (top seeds get byes, lower seeds drop to a smaller consolation
ladder), which would corrupt All-Play/median comparisons that assume a
consistent field size. Playoff outcomes (championships, bracket runs)
are a separate Hall-of-Fame concern for Phase 6, read directly from
`matchups.matchup_type`/`is_playoff`, not blended into this weekly
snapshot. **This reconciliation-against-ESPN's-own-numbers technique is
worth reusing whenever a new derived metric is added** - it caught a real
design bug (silently including playoff weeks) that unit tests alone
would not have caught, since the mock-data unit tests can't know what
ESPN's real semantics are.

Validated: computed matchup+median combined record and points_for match
ESPN's official numbers EXACTLY for every team in a median-scoring season
(2025) and a pre-median season (2020) at the regular-season-end
checkpoint. `luck_wins` is deliberately matchup-only (not the combined
record) against All-Play expected wins, isolating opponent-schedule luck
from the median bonus (which isn't opponent-dependent) - see the
in-code comment on `metrics_weekly` in `db.py` and the docstring in
`season_metrics.py` for the full reasoning, and the "median scoring
toggle" unit test that asserts `luck_wins` doesn't move when the median
toggle changes but `actual_win_pct`/`fraud_index` do.

Metrics currently computed for all 11 completed seasons (2015-2025);
2026 has 0 rows so far because week 1 hasn't finished yet (`completed=0`)
- this is expected, not a bug, and will populate on the next refresh
after week 1 concludes.

**Phase 4 (DONE 2026-09-13):** Streamlit Home, Matchups, and Luck pages.
New: `Home.py` (entrypoint), `pages/1_Matchups.py`, `pages/2_Luck.py`,
`fantasy_football/dashboard_data.py` (Streamlit-cached read-model queries
- this layer IS allowed to depend on Streamlit, unlike `metrics/`),
`fantasy_football/ui_common.py` (shared CSS/cards/badges/sidebar),
`fantasy_football/badges.py` (Fraud Index thresholds), and
`fantasy_football/metrics/win_probability.py` (+ unit tests) for the
Matchups page's custom (non-ESPN) win probability on not-yet-played
games. Also extended `ingest.py` with `ingest_future_schedule()` so the
current season's upcoming regular-season matchup pairings (score=NULL,
completed=0) get pulled - previously ingestion only went up through
`current_week`, which meant no schedule existed yet for future weeks.

**Tested by actually running the app** (per project convention - not just
"should work"): started the Streamlit server, drove it with Playwright
(headless Chromium at `/opt/pw-browsers/chromium`), and found three real
bugs this way that unit tests / code review would not have caught:
1. `dashboard_data.get_connection()` was wrapped in `@st.cache_resource`,
   caching a raw `sqlite3.Connection` - but Streamlit can rerun a
   session's script on a different thread, and sqlite3 connections are
   thread-affine, so this threw `SQLite objects created in a thread can
   only be used in that same thread` on the second page load. Fixed by
   NOT caching the connection (opening a fresh one per call is cheap for
   a local file).
2. The sidebar's season/week selectors reset to the default every time
   you navigated to a different page (Home -> Luck -> Matchups), because
   each page's `st.selectbox(..., index=seasons.index(seasons[0]))` call
   recomputed a hardcoded default with no persistence. Tried
   `st.query_params` first - discovered empirically that Streamlit's
   built-in multipage sidebar nav links reset the URL's query string on
   every page switch (a known Streamlit limitation, not something we can
   fix from app code), so that approach can't work here. Fixed instead
   with `st.session_state` (confirmed with an isolated minimal 2-page
   repro app that state genuinely persists across page navigation via
   session_state, unlike query_params).
3. A team name with a trailing space ("The Brown Downs ") broke Markdown
   bold rendering (`**The Brown Downs **` isn't valid CommonMark bold,
   since a closing `**` can't be preceded by whitespace) - showed up as
   literal asterisks on the Matchups page. Fixed with `.strip()` before
   wrapping in `**`.

Validated against real data on the 2025 season (fully completed): Home
page standings/power rankings/headline cards render correctly and match
previously-validated metrics; Luck page's Actual/Expected/All-Play
records, Fraud badges, and the matchup-vs-median breakdown expander all
check out; Matchups page renders played-game scores correctly, and the
win-probability function for not-yet-played games was verified directly
against real data (`dd.project_matchup_win_probability` returns a valid
0-1 probability). The "not enough data yet" empty states for the
in-progress 2026 season (0 completed weeks so far) render correctly
without crashing rather than showing fabricated data.

**Known gaps intentionally left for later phases** (not bugs - matches
spec's "never fabricate missing data" principle): no Playoff Probability
column or Projected Champion card (Phase 7, needs Monte Carlo sim), no
per-team short commentary on Home (Phase 8, needs the commentary
module), no historical head-to-head on Matchups cards (Phase 6).

**Roster Strength (DONE 2026-09-13, out-of-sequence addition):** A new
forward-looking metric, requested mid-Phase-4 - distinct from Power
Score (backward-looking, based on results already produced). New:
`fantasy_football/metrics/roster_strength.py` (+ unit tests),
`pages/3_Roster_Strength.py`, `player_rankings` and
`roster_strength_weekly` tables, `ingest.py:ingest_player_rankings()`.

**Data sourcing decision (important, don't revisit without checking
this first):** the user originally wanted 4 sources blended (ESPN
weekly projection, ESPN season rank, FantasyPros ROS rankings, Yahoo ROS
rankings). Investigated both before building: FantasyPros'
`/about/legal/` Terms of Use explicitly prohibit automated reproduction
("you may not copy, reproduce, modify, republish, upload, post,
transmit, or distribute any documents or information from this site"
beyond "a single copy made for personal use") - their `robots.txt`
doesn't block the rankings pages, but ToS is the controlling document,
not robots.txt, so an automated scraper is out unless/until the user
gets licensed API access (`fantasypros.com/api-data/`, requires
signup/partnership, not free/instant). Yahoo has no generic
rest-of-season-rankings endpoint - their Fantasy Sports API is scoped to
leagues you're an OAuth-authenticated participant in, not a general
rankings feed. **Decision: v1 ships ESPN-only** (weekly projection +
`posRank` from `league.player_info()`, weights 20:15 carried over
proportionally from the original 4-source design). Revisit FantasyPros
only if the user actually obtains a licensed API key - don't scrape
around the ToS.

`league.player_info(playerId=<list>)` accepts a batched list and
returns `posRank`/`percent_owned`/`percent_started` for every ID in one
call (confirmed: 207 rostered players in ~1s) - use this, not one call
per player. `posRank` comes back as `0` (not populated/unranked) for
un-droppable... actually for undrafted/deep bench rookies - store as
NULL, not 0, since 0 reads as "rank zero" if anyone queries the raw
table directly (`rank_to_score()` already guarded against this
correctly, but the raw storage was cleaned up too for clarity).

Bench-vs-starter weighting: bench share decays from 35% (week 1) to a
10% floor by the end of the regular season and HOLDS at that floor
through the playoffs - it does not go to zero. This was a direct user
correction to the first design: bye-week-driven bench value fades once
byes are over (~week 14, confirmed via 2026 NFL bye schedule research:
byes run weeks 5-14), but injury-replacement value never disappears.
See `bench_weight_for_week()` in `roster_strength.py`.

Only ever populated for the CURRENT week of the CURRENT season -
`posRank` has no history in ESPN's API, same backfill limitation as
`recent_activity()`. Empty-state handled gracefully on the page (not
fabricated) for any season/week combo where it hasn't run.

**Phase 5 (DONE 2026-09-13):** Manager lineup efficiency / optimal
lineup engine. New: `fantasy_football/metrics/lineup_optimizer.py` (+
unit tests) - an EXACT optimal-lineup solver via scipy's Hungarian
algorithm (maximum-weight bipartite matching, players x starting slots),
not a greedy heuristic - a unit test specifically constructs a flex-slot
contention scenario where naive greedy would misallocate and confirms
the exact solver gets it right. `fantasy_football/metrics/
lineup_efficiency.py` (+ tests) computes actual vs. optimal starter
points, lineup efficiency, points left on bench, an optimal-lineup
win/loss record (recomputed against the opponent's REAL actual score),
and manager-caused-loss detection. `pages/4_Lineup_Efficiency.py`.

**Schema change needed real data captured that wasn't being stored:**
per-player slot eligibility (`Player.eligibleSlots` from espn-api) is
required to know which slots a player could legally have filled, and it
wasn't in `weekly_rosters` before this phase. Added `weekly_rosters.
eligible_slots` (JSON, snapshotted per week since ESPN eligibility can
shift mid-season) and 4 new `metrics_weekly` columns for the
lineup-efficiency fields. **Both tables already held real ingested
data**, so `db.py` gained its first schema migration helper
(`_apply_migrations` / `MIGRATIONS` dict, `ALTER TABLE ... ADD COLUMN`,
idempotent) rather than the drop-and-recreate approach used earlier in
Phase 3 when `metrics_weekly` was still empty - future column additions
to a populated table should use this same pattern, not a destructive
recreate.

Backfilled `eligible_slots` for all existing 2019-2026 weeks via a full
re-ingest (`python refresh_data.py 2019 2020 2021 2022 2023 2024 2025
2026`, ~207s) - necessary because this data wasn't being captured
before, same category of "wish we'd captured this from the start" as
the week-14 bye-week bug back in Phase 2. Validated against real data:
zero rows with `lineup_efficiency > 1.0` (would indicate the optimal
solver is broken - actual can never legitimately exceed a correctly-
computed optimal), 2025 season efficiency range 87-94% across all 12
teams (a plausible real-world range - nobody starts a perfect lineup
every week, but nobody is wildly far off either). Confirmed correct
rendering in-browser via Playwright.

Only computable for year>=2019 (needs box-score-derived per-player
data, same constraint as Roster Strength/player-level ingestion
generally) and only for weeks with `eligible_slots` captured (now true
for all of 2019-2026 after the backfill above). `compute_and_store_
lineup_efficiency()` UPDATES existing `metrics_weekly` rows rather than
inserting fresh ones - it has a hard ordering dependency on
`compute_and_store_season_metrics()` having already run for that season
(otherwise an upsert of a partial column set would INSERT a new row
with every OTHER column NULL) - `compute_and_store_all_seasons()`
enforces this ordering already, don't call
`compute_and_store_lineup_efficiency()` standalone without checking
that dependency still holds.

**Decision Accuracy (DONE 2026-09-13, user-requested addition to Phase 5):**
a points-BLIND companion to Lineup Efficiency. The user's insight: a
single boom/bust bench player dominates the POINTS-based efficiency
metric (one huge outlier game makes the gap look enormous) even though
it only represents ONE wrong start/sit call. Decision Accuracy instead
compares the SET of players actually started against the SET the
optimal lineup would have started (`len(actual ∩ optimal) /
len(optimal)`), so one wrong swap always reads as exactly one wrong
decision regardless of how many points it was worth. New columns on
`metrics_weekly`: `correct_decisions`, `total_decisions`,
`decision_accuracy` (added via the same `db.py` migration pattern as
the rest of Phase 5, since the table already had data again).

Validated with a dedicated unit test constructing exactly this
scenario (4 correctly-started players + 1 wrong swap worth a 45-point
swing) - lineup_efficiency reads a misleadingly bad 50%, decision_
accuracy correctly reads 80% (4/5). Also validated against real 2025
season data: range 55-91% across all team-weeks, `total_decisions` for
a full season = 154 (11 starting slots × 14 weeks, confirms the
denominator is right), and per the 2025-week-14 standings the two
metrics visibly reorder teams relative to each other (not just a
linear rescaling of the same signal) - e.g. "Thankful for Coffeys Team"
ranks #1 by points-efficiency but only #4 by decision accuracy.
Recomputing this needed no re-ingest, only a metrics recompute
(`compute_and_store_all_seasons`), since it's derived entirely from
data already captured in the Phase 5 backfill.

**Phase 6 (DONE 2026-09-13):** History / Hall of Fame / League Records /
Head-to-Head rivalry explorer. New: `fantasy_football/metrics/
history.py` (+ unit tests - pure functions per the spec's testing
requirement: `compute_streaks`, `compute_head_to_head`,
`compute_league_records`), `fantasy_football/history_data.py`
(Streamlit-cached read-model spanning ALL seasons, unlike
`dashboard_data.py` which is scoped to one season), `pages/5_History.py`
(3 tabs: Hall of Fame, League Records, Head-to-Head).

**Major correctness discovery while validating this phase: ESPN member
IDs are not as permanently stable as the espn-api docs / this project's
own earlier assumption implied.** For the 2026 season, ESPN listed a
freshly re-linked account as an ADDITIONAL co-owner alongside 2
managers' (Aaron Hendel, Alex Bradford) long-standing member ids -
confirmed same real people by matching first/last name, and by the
"new" id having zero history before 2026 while the "old" id has the
full 2015-2026 span. The app's original "primary owner = alphabetically
first manager_id" convention (used since Phase 4) picked whichever id
sorted first, which for one of these two people picked the NEW
(historyless) id for the 2026 season specifically - silently splitting
that person's whole career across two Hall of Fame rows, and would have
caused the SAME misattribution on Home/Luck/Roster Strength/Lineup
Efficiency's "Manager" column for their current-season row too (not
just History) had it gone unnoticed.

Fixed at the shared root, not per-page: added `db.primary_owner_join_sql()`
and `db.primary_manager_ids_sql()` (built on a common `_ranked_owners_sql()`
window-function query) that rank co-owners by TOTAL TENURE (count of
teams/seasons owned league-wide) rather than picking alphabetically -
self-correcting if this happens again for someone else in a future
season. `dashboard_data.py._team_manager_join_sql()` and
`history_data.py._primary_manager_sql()` both now delegate to this one
definition instead of maintaining separate (and, it turned out,
differently-buggy) copies. `history_data.get_managers()` also had to be
fixed separately - it was listing every raw `team_owners.manager_id`
including non-primary aliases, which produced a confusing duplicate-
looking entry in the Head-to-Head picker that could never actually
match any matchup (since matchup resolution always used the corrected
primary-owner logic) - now built on `primary_manager_ids_sql()` so the
two can't drift apart again.

Two more real bugs found via in-browser validation (Playwright), both
in `history.py`: (1) "Closest Game" and "Biggest Blowout" displayed the
raw SIGNED margin instead of its absolute value - showed "-0.1 pt
margin" when the losing side's row happened to be selected by
`idxmin()`. Fixed with `abs()`, and strengthened the unit test to
include the losing side's mirror row specifically so it would have
caught this (the original test only had one row per game, which
happened to always be positive and couldn't expose the bug). (2) The
page displayed raw manager_id GUIDs instead of display names for
"Largest victory" and "Current Streak" - `compute_head_to_head()`
correctly returns manager_id (staying ID-based keeps the pure function
testable/agnostic of display concerns), but the page forgot to map IDs
back to names before rendering; fixed in `pages/5_History.py`, not the
pure function.

Cross-season design maintained consistently: Hall of Fame's
championships/finals/playoff-appearances/career-record are exact facts
(a win is a win regardless of scoring format); "Best/Worst Season" uses
season-relative PPG percentile (`metrics_weekly.ppg_percentile` at each
season's final week), NOT raw points, for the reasons documented back
in Phase 3; "Career Points (raw)" is shown for reference only, explicitly
labeled as not a fair ranking basis. League Records are raw record-book
facts spanning every season/era on purpose (a record is a record,
era and all) - not used for any manager-vs-manager ranking claim.

Validated cross-referencing the GroupMe research from earlier in this
session: Nick McGillivray's 3 championships (2020, 2022, 2025) exactly
match the "2nd ship in 3 years" (2022) and "dynasty"/"3rd ship" (2025)
chat references found during the personality-mining conversation - a
good independent confirmation the History calculations are correct,
not just internally consistent.

**Phases 7-8:** Not started (Playoff simulation; Weekly recap + Claude
commentary).

**FantasyPros integration into Roster Strength (DONE 2026-09-13).** The
user purchased FantasyPros API access; the key is stored as
`FANTASYPROS_API_KEY` in `.env` (gitignored, same pattern as
`ESPN_S2`/`SWID`). New: `fantasy_football/fantasypros_client.py` (thin
wrapper - `fetch_ros_rankings()`/`fetch_all_ros_rankings()` against
`api.fantasypros.com/public/v2/json/nfl/{year}/consensus-
rankings?type=ROS&position={POS}`, auth via `x-api-key` header, covers
all 6 standard positions: QB/RB/WR/TE/K/DST), `fantasy_football/
player_matching.py` (+ unit tests - pure, no network/DB), new
`fantasypros_rankings` table, `ingest.py:ingest_fantasypros_rankings()`.

**Cross-platform player ID matching** (the hard part of this task,
since FantasyPros and ESPN share no common player id): matches by
normalized name within position for QB/RB/WR/TE/K (`normalize_name()`
lowercases, drops apostrophes, turns hyphens/periods into spaces, and
strips a trailing Jr/Sr/II/III/IV/V suffix, so "Kenneth Walker III"
collides with "Kenneth Walker"), and by NFL team abbreviation for D/ST
specifically - name matching doesn't work there at all (ESPN stores
"Texans D/ST", FantasyPros returns "Houston Texans", zero shared name
tokens). Confirmed ESPN's `pro_team` and FantasyPros' `player_team_id`
agree on 30 of 32 team abbreviations; the 2 that differ (`JAX`/`JAC`,
`WSH`/`WAS`) are hardcoded in `TEAM_ABBREV_FP_TO_ESPN`. An ambiguous
name (2+ currently-rostered players sharing a normalized name at the
same position) is deliberately left unmatched rather than guessed -
hasn't happened in this league's real rosters yet, but the logic
handles it safely if it ever does. Matching only runs against players
actually on a roster this season/week (not FantasyPros' full league-
wide player pool per position), which also keeps the false-collision
search space small. **Validated against real 2026 week-1 data: 207/207
rostered players matched (100%), including all 14 rostered D/ST units**
- see `_match_dst`/`_match_by_name` in `player_matching.py` for the two
matching strategies and their respective unit tests.

**Reweighted `SIGNAL_WEIGHTS`** in `roster_strength.py` per the
original 4-source design: FantasyPros ROS rank 40, ESPN weekly
projection 20, ESPN season rank 15 (Yahoo's 25 dropped, remaining 3
renormalized to sum to 1: 20/75, 15/75, 40/75). `compute_player_values`'
`blend()` was generalized from a 2-signal special case to N present
signals, renormalizing over whichever signals actually exist for a
given player (a player FantasyPros couldn't match, or whose ESPN
posRank isn't populated yet, correctly falls back to the signals it
does have rather than being penalized as if the missing signal were a
zero) - see the new `test_player_value_blends_fantasypros_signal_when_
present`/`test_player_value_falls_back_when_fantasypros_column_absent`
tests, which specifically assert this renormalization (not just "does
it run without crashing").

Same "current week only, no backfill" limitation as ESPN's `posRank` -
FantasyPros' ROS endpoint has no history either. Validated end-to-end
against real 2026 week-1 data via `refresh_data.py`-equivalent manual
run: `roster_strength_weekly` populated for all 12 teams, plausible
range (37.7-50.4 on the 0-100 scale, week 1 - expect this range to
shift once real week 1 results roll in and ESPN's season-long
`posRank` starts meaningfully differentiating players). Confirmed
correct rendering in-browser via Playwright, including the updated
Methodology expander. 37/37 unit tests passing (`tests/test_metrics.py`
+ new `tests/test_player_matching.py`).

**Lineup Efficiency scope filter (DONE 2026-09-13, user-requested
addition to Phase 5):** added a Regular Season / Playoffs / All radio
filter to `pages/4_Lineup_Efficiency.py`. "Playoffs" means true
championship-bracket weeks ONLY (`matchups.matchup_type =
'WINNERS_BRACKET'`) - deliberately excludes `LOSERS_CONSOLATION_LADDER`/
`WINNERS_CONSOLATION_LADDER` (never a shot at the title) and playoff bye
weeks (a team with no matchup row that week is simply absent, not zero-
filled). "All" = regular season + championship-bracket weeks combined,
consolation still excluded. New: `_scope_matchup_type_filter()`,
`load_matchups_by_scope()`, `load_roster_with_points_by_scope()` in
`metrics/loaders.py` (the existing regular-season-only loaders used by
the persisted `metrics_weekly` pipeline are untouched); extended
`compute_lineup_efficiency()` to also output the real `matchup_wins/
losses/ties` (previously only had the optimal-lineup record, missing
the actual one); `dashboard_data.get_lineup_efficiency_by_scope()`
reuses the persisted regular-season table for that scope and computes
playoffs/all live (cheap - one season's data, no live ESPN calls).

**User-confirmed design intent, don't "fix" this later:** under "All"
scope, a team with fewer playoff appearances naturally contributes
fewer weeks to its own Decision Accuracy numerator/denominator (e.g. a
team that didn't make the playoffs stays at the regular-season total of
154 decisions, while a team that advanced further accumulates more) -
this is correct and intentional per the user's explicit instruction
("less shots at it"), not a normalization bug to paper over.

Validated against real 2025 season data: `regular` scope -> weeks 1-14;
`playoffs` scope -> weeks 15-17 with team counts 4/4/2 per week
(correctly isolating just the championship path); `all` -> weeks 1-17.
Confirmed in-browser via Playwright for all 3 scopes, zero errors -
e.g. "The Quest for Three" (made it deep into the 2025 playoffs) shows
162/187 decisions under "All" vs. 130/154 for a team that didn't make
the playoffs, exactly the expected effect. 29/29 unit tests passing
(`tests/test_metrics.py`), including a strengthened
`test_lineup_efficiency_flags_manager_caused_loss` that now also
asserts the real `matchup_wins/losses/ties` output.
Note: the user has begun gathering manager personality/context material
from GroupMe (`Salted by Quincy` and `Pike Fantasy football` group chats)
for eventual use in Phase 8 commentary - reviewed in chat, NOT yet
written to the database per the user's explicit request ("will come back
and edit later"). Don't add a personalities/bios table or column on your
own initiative; wait for the user to provide the finalized version.

**First actions for a new session:**
1. `cd` into the repo, run `python test_connection.py` (venv should exist
   at `venv/` - recreate with `python3 -m venv venv && venv/bin/pip
   install -r requirements.txt` if the sandbox is fresh and venv/ isn't
   present - note venv/ is not gitignored-safe to assume persists across
   sessions, check first). Confirm the .env file is present and has real
   values before running - if missing, ask the user for
   LEAGUE_ID/ESPN_S2/SWID again. If asking the user to paste cookie values
   from browser DevTools, tell them explicitly to check "Show URL-decoded"
   in the Cookies panel first (see RESOLVED note above) - otherwise
   they'll paste a `%2F`/`%2B`/`%3D`-encoded value that gets rejected with
   a misleading 401 `AUTH_LEAGUE_NOT_VISIBLE`.
2. Check `data/league.db` exists and has all 12 seasons (run
   `sqlite3 data/league.db "SELECT season_id FROM seasons ORDER BY 1"` or
   equivalent) - if incomplete, run `python refresh_data.py` (idempotent).
   Note `data/` is gitignored, so a fresh clone/session needs a fresh
   `python refresh_data.py` run regardless.
3. Proceed to Phase 3 (metrics: All-Play, Luck, Fraud, Power Rankings) -
   read the two IMPORTANT callouts in the Luck dashboard and History
   sections above first, they change how "wins" and cross-season
   comparisons must be computed for this league.
