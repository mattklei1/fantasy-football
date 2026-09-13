"""WAR ROOM page: commissioner-only tools - full FantasyPros data access,
a sharper waiver model, and a trade calculator. Visible in navigation to
everyone (deliberately, per the commissioner's request - the locked
teaser is half the fun), but only fantasy_football.ui_common.is_admin()
renders real content; everyone else sees a locked screen. Feature build
(trade calculator, advanced suggestions) is scaffolding only for now -
see PROJECT_BRIEF for the roadmap."""
from __future__ import annotations

import streamlit as st

from fantasy_football import ui_common as ui

st.set_page_config(page_title="War Room", page_icon="🔒", layout="wide")
ui.inject_css()

season, week = ui.render_sidebar()

if not ui.is_admin():
    st.title("🔒 War Room")
    st.markdown(
        "### Commissioner-only\n"
        "This page is locked. Ask the commissioner nicely."
    )
    st.stop()

st.title("🔒 War Room")
st.caption("Commissioner-only - not visible to anyone else on this deployment.")

st.info(
    "Scaffolding only right now: the access gate is live, but the actual tools below aren't "
    "built yet. Roadmap - full third-party rankings browser, an upgraded waiver-value model with "
    "more inputs than the public report uses, and a trade calculator."
)

st.markdown("#### Coming soon")
st.markdown(
    "- Full third-party rankings/projections browser (every position, every scoring format)\n"
    "- Advanced waiver suggestions (beyond `metrics/waiver_value.py`'s public-facing model)\n"
    "- Trade calculator (value-based, superflex-aware)\n"
)
