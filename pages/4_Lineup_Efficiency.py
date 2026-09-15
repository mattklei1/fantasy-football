"""LINEUP EFFICIENCY page: Actual vs. Optimal starter points, and
whether a different legal lineup would have won the matchup. Filterable
by Regular Season / Playoffs (every real playoff-bracket week - same
definition as the History page's Playoff Record, see metrics/history.
REAL_PLAYOFF_MATCHUP_TYPES) / All. Supports "All time" (every season
combined, aggregated by manager identity since team_pk resets every
season) alongside a single season."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import history_data as hd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Lineup Efficiency", page_icon="🧠", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar(support_all_time=True)
all_time = season == "All time"

st.title("Lineup Efficiency")

scope_label_to_value = {"Regular Season": "regular", "Playoffs": "playoffs", "All": "all"}
scope_label = st.radio("Scope", list(scope_label_to_value.keys()), horizontal=True)
scope = scope_label_to_value[scope_label]

if scope == "playoffs":
    st.caption(
        "Championship-bracket weeks only - same definition as History's Playoff Record. "
        "Excludes both of ESPN's placement/consolation ladders (one for teams that missed the "
        "playoffs, one for teams that qualified but lost early - neither had a title on the "
        "line) and playoff bye weeks (no matchup row exists for one)."
    )
elif scope == "all":
    st.caption(
        "Regular season + championship-bracket weeks combined (see Playoffs scope above for "
        "exactly what counts). Teams with fewer playoff appearances naturally contribute fewer "
        "weeks to their own numerator/denominator here - that's correct, not a bug: fewer shots "
        "at it."
    )

df = hd.get_all_time_lineup_efficiency(scope) if all_time else dd.get_lineup_efficiency_by_scope(season, scope)

if df.empty:
    if all_time:
        st.info(
            "No lineup efficiency data available yet across any season. This requires per-player "
            "slot eligibility data that's only available for 2019 onward."
        )
    elif scope == "regular":
        st.info(
            "No lineup efficiency data for this season yet. This requires per-player slot "
            "eligibility data that's only available for 2019 onward, and only for weeks "
            "ingested since this feature shipped (2026-09-13) - run a refresh to backfill."
        )
    else:
        st.info(f"No {scope_label.lower()} lineup data for this season (no qualifying weeks played yet).")
    st.stop()

if all_time:
    st.caption(f"Every season combined (2019+) · {scope_label.lower()} · ranked by career Lineup Efficiency")
elif scope == "regular":
    st.caption(f"Through week {week} (regular season) · ranked by season-to-date Lineup Efficiency")
else:
    st.caption(f"Full-season {scope_label.lower()} totals · ranked by Lineup Efficiency")

df = df.reset_index(drop=True)
df.insert(0, "Rank", df.index + 1)

display = df.copy()
display["Actual Record"] = display.apply(
    lambda r: ui.format_record(r["matchup_wins"], r["matchup_losses"], r["matchup_ties"]), axis=1
)
display["Optimal-Lineup Record"] = display.apply(
    lambda r: ui.format_record(r["optimal_wins"], r["optimal_losses"], r["optimal_ties"]), axis=1
)
display["Efficiency"] = (display["lineup_efficiency"] * 100).round(1).astype(str) + "%"
display["Actual Pts"] = display["actual_starter_points"].round(1)
display["Optimal Pts"] = display["optimal_starter_points"].round(1)
display["Left on Bench"] = display["points_left_on_bench"].round(1)
display["Manager-Caused Losses"] = display["manager_caused_losses"].astype(int)
display["Decision Accuracy"] = (display["decision_accuracy"] * 100).round(1).astype(str) + "%"
display["Decisions"] = (
    display["correct_decisions"].astype(int).astype(str) + "/" + display["total_decisions"].astype(int).astype(str)
)

if all_time:
    display["Seasons"] = display["seasons_played"].astype(int)
    columns = [
        "Rank", "manager_name", "Seasons", "Efficiency", "Decision Accuracy", "Decisions",
        "Actual Pts", "Optimal Pts", "Left on Bench", "Actual Record", "Optimal-Lineup Record",
        "Manager-Caused Losses",
    ]
else:
    columns = [
        "Rank", "team_name", "manager_name", "Efficiency", "Decision Accuracy", "Decisions",
        "Actual Pts", "Optimal Pts", "Left on Bench", "Actual Record", "Optimal-Lineup Record",
        "Manager-Caused Losses",
    ]

table = display[columns].rename(columns={"team_name": "Team", "manager_name": "Manager"})

st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)

with st.expander("Methodology"):
    st.markdown(
        """
For every completed week, the **optimal legal lineup** is computed exactly (not a heuristic) -
an exact maximum-weight assignment of rostered players to starting slots, respecting each
player's real slot eligibility and the league's real roster settings pulled from ESPN
(`league.settings.position_slot_counts` - never hardcoded).

- **Scope**: Regular Season (weeks with `matchup_type = NONE`), Playoffs (`WINNERS_BRACKET`
  only - the real championship bracket, same definition as the History page's Playoff Record),
  or All (both combined). Both of ESPN's placement/consolation ladders (one for teams that
  missed the playoffs, one for teams that qualified but lost early) and playoff bye weeks are
  excluded from every scope - a bye week has no matchup row to draw a lineup eligibility
  snapshot from in the first place, and neither consolation ladder had a title on the line.
- **Lineup Efficiency** = Actual Starter Points ÷ Optimal Starter Points, summed over the
  selected scope's weeks
- **Decision Accuracy** = a points-BLIND companion metric: compares the SET of players you
  actually started against the SET the optimal lineup would have started, and counts how many
  of your N starting-slot decisions matched. A single boom/bust bench player dominates Lineup
  Efficiency (one huge outlier game makes the points gap look enormous) but is still just ONE
  wrong swap - Decision Accuracy reflects that correctly regardless of how many points that
  swap was worth. Read the two together: low efficiency + high decision accuracy usually means
  "one bad beat," not "bad process."
- **Optimal-Lineup Record** recomputes each week's result using the optimal lineup's points
  against the opponent's real actual score, then aggregates to a record just like the real one
- **Manager-Caused Losses** counts weeks where the optimal lineup would have won, but the
  real lineup didn't - a loss caused by a lineup decision, not bad luck

Only available for 2019+ (needs per-player box-score data) and only for weeks ingested since
this feature shipped - no backfill exists for weeks ingested before eligibility tracking began.
        """
    )
