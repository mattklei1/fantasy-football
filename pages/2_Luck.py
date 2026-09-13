"""LUCK page: All-Play record, Luck Wins, Fraud Index, and the
matchup-vs-median decomposition for seasons using the top-half bonus."""
from __future__ import annotations

import streamlit as st

from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui
from fantasy_football.badges import fraud_badge

st.set_page_config(page_title="Luck", page_icon="🍀", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar()
meta = dd.get_season_meta(season)
median_scoring = bool(meta.get("median_scoring"))

st.title("Luck & Fraud")

standings = dd.get_standings(season, through_week=week)
if standings.empty:
    st.info("No completed regular-season weeks yet for this season.")
    st.stop()

st.caption(f"Through week {week} (regular season only)")

df = standings.copy()
df["points_against_rank"] = df["points_against"].rank(ascending=True, method="min").astype(int)
df["luck_rank"] = df["luck_wins"].rank(ascending=False, method="min").astype(int)
df = df.sort_values("luck_rank")

# Matchup Record (NOT the combined actual/official record) - deliberately
# apples-to-apples with Expected Record below, since Luck Wins itself
# compares matchup-only wins to matchup-only expected wins (see
# season_metrics.py's comment on why median is excluded from luck). Pairing
# the OFFICIAL combined record against a matchup-only expectation here would
# look like a huge, confusing gap for any median-scoring season even though
# the underlying Luck Wins number is correct - that mismatch is exactly
# what prompted this fix (2026-09-13).
df["Matchup Record"] = df.apply(
    lambda r: ui.format_record(r["matchup_wins"], r["matchup_losses"], r["matchup_ties"]), axis=1
)
# Expected record shown as expected wins out of games played (simpler, unambiguous
# than trying to force it into a W-L string)
df["Expected Record"] = df.apply(lambda r: f"{r['expected_wins']:.1f} exp. wins / {r['games_played']} gp", axis=1)
df["All-Play Record"] = df.apply(
    lambda r: ui.format_record(r["all_play_wins"], r["all_play_losses"], r["all_play_ties"]), axis=1
)
# Official Record (matchup + median combined) - the SAME record shown on
# Home's standings table. Fraud Index is computed from this combined
# record (actual_win_pct includes the median bonus when applicable - see
# season_metrics.py), so it needs to be visible here too, not just the
# matchup-only record above (which Luck Wins/Expected Record use instead) -
# otherwise the Fraud badge looks like it's based on nothing shown on this
# page at all.
df["Official Record"] = df.apply(
    lambda r: ui.format_record(
        r["matchup_wins"] + (r["median_wins"] or 0),
        r["matchup_losses"] + (r["median_losses"] or 0),
        r["matchup_ties"] + (r["median_ties"] or 0),
    ),
    axis=1,
)
df["Fraud"] = df["fraud_index"].apply(lambda f: ui.badge_html(fraud_badge(f)))
df["Luck Wins"] = df["luck_wins"].round(2)

table = df[
    ["luck_rank", "team_name", "Matchup Record", "Expected Record", "Luck Wins",
     "points_against_rank", "All-Play Record", "Official Record", "Fraud"]
].rename(columns={
    "luck_rank": "Luck Rank", "team_name": "Team", "points_against_rank": "Pts Against Rank",
})

st.markdown(table.to_html(escape=False, index=False, classes="ff-table"), unsafe_allow_html=True)
if median_scoring:
    st.caption(
        "Matchup Record excludes the median (top-half) bonus on purpose - it's what Luck "
        "Wins/Expected Record are actually computed from. Official Record (matchup + median "
        "combined, matching Home's standings) is what Fraud Index and the Fraud badge are "
        "computed from instead - the two records intentionally feed different metrics, see "
        "the breakdown below for both side by side."
    )

if median_scoring:
    with st.expander("Matchup record vs. Median (top-half) record breakdown"):
        st.caption(
            "This league uses ESPN's top-half scoring bonus - your official record each week "
            "is your real matchup result PLUS a bonus win/loss for finishing top-half or "
            "bottom-half in scoring league-wide. Luck Wins above uses the MATCHUP record only "
            "(isolating opponent-schedule luck); the median result isn't opponent-dependent so "
            "it isn't 'luck' in that sense - shown here separately for transparency."
        )
        breakdown = df.copy()
        breakdown["Matchup Record"] = standings.apply(
            lambda r: ui.format_record(r["matchup_wins"], r["matchup_losses"], r["matchup_ties"]), axis=1
        )
        breakdown["Median Record"] = standings.apply(
            lambda r: ui.format_record(r["median_wins"] or 0, r["median_losses"] or 0, r["median_ties"] or 0),
            axis=1,
        )
        st.markdown(
            breakdown[["team_name", "Matchup Record", "Median Record"]]
            .rename(columns={"team_name": "Team"})
            .to_html(escape=False, index=False, classes="ff-table"),
            unsafe_allow_html=True,
        )

st.markdown("### Notable")
extras = dd.get_luck_page_extras(season)
if extras:
    c1, c2, c3, c4 = st.columns(4)
    hsl = extras.get("highest_score_in_loss")
    lsw = extras.get("lowest_score_in_win")
    with c1:
        if hsl is not None:
            ui.stat_card("Highest Score in a Loss", f"{hsl['score']:.1f}", f"{hsl['team_name']} (Wk {int(hsl['week'])})")
    with c2:
        if lsw is not None:
            ui.stat_card("Lowest Score in a Win", f"{lsw['score']:.1f}", f"{lsw['team_name']} (Wk {int(lsw['week'])})")
    with c3:
        ui.stat_card("Top-3 Scores That Still Lost", str(extras.get("top3_scores_that_lost", 0)))
    with c4:
        ui.stat_card("Bottom-3 Scores That Still Won", str(extras.get("bottom3_scores_that_won", 0)))
