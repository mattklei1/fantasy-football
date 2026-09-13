"""Shared Streamlit presentation helpers: theme CSS, sidebar (league info,
season switcher, refresh button), and small formatting utilities reused
across Home.py and pages/*.py."""
from __future__ import annotations

import streamlit as st

from . import dashboard_data as dd
from .badges import fraud_badge

BADGE_COLORS = {
    "LEGIT": "#2e7d32",
    "SLIGHTLY SUSPICIOUS": "#b8860b",
    "FRAUD WATCH": "#c9622a",
    "GENERATIONAL FRAUD": "#b71c1c",
}

CSS = """
<style>
:root {
    --card-bg: #ffffff;
    --card-border: #e5e7eb;
    --text-muted: #6b7280;
    --accent: #4f46e5;
}
@media (prefers-color-scheme: dark) {
    :root {
        --card-bg: #1f2430;
        --card-border: #333a4a;
        --text-muted: #9aa2b1;
        --accent: #818cf8;
    }
}
.ff-card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 8px;
}
.ff-card-label {
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-muted);
    font-weight: 600;
    margin-bottom: 4px;
}
.ff-card-value {
    font-size: 1.5rem;
    font-weight: 700;
    line-height: 1.2;
}
.ff-card-sub {
    font-size: 0.85rem;
    color: var(--text-muted);
    margin-top: 2px;
}
.ff-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 700;
    color: white;
    letter-spacing: 0.02em;
}
.ff-rank-up { color: #2e7d32; font-weight: 700; }
.ff-rank-down { color: #b71c1c; font-weight: 700; }
.ff-rank-flat { color: var(--text-muted); }
table.ff-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
}
table.ff-table th {
    text-align: left;
    padding: 8px 10px;
    border-bottom: 2px solid var(--card-border);
    color: var(--text-muted);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
table.ff-table td {
    padding: 8px 10px;
    border-bottom: 1px solid var(--card-border);
}
table.ff-table tr:hover td {
    background: rgba(79, 70, 229, 0.06);
}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def stat_card(label: str, value: str, sub: str | None = None) -> None:
    sub_html = f'<div class="ff-card-sub">{sub}</div>' if sub else ""
    st.markdown(
        f'<div class="ff-card"><div class="ff-card-label">{label}</div>'
        f'<div class="ff-card-value">{value}</div>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def badge_html(label: str) -> str:
    color = BADGE_COLORS.get(label, "#6b7280")
    return f'<span class="ff-badge" style="background:{color}">{label}</span>'


def fraud_badge_html(fraud_index: float | None) -> str:
    return badge_html(fraud_badge(fraud_index))


def rank_change_html(change) -> str:
    if change is None or (isinstance(change, float) and change != change):  # NaN
        return '<span class="ff-rank-flat">–</span>'
    change = int(change)
    if change > 0:
        return f'<span class="ff-rank-up">▲ {change}</span>'
    if change < 0:
        return f'<span class="ff-rank-down">▼ {abs(change)}</span>'
    return '<span class="ff-rank-flat">–</span>'


def format_record(wins, losses, ties=0) -> str:
    wins, losses, ties = int(wins or 0), int(losses or 0), int(ties or 0)
    if ties:
        return f"{wins}-{losses}-{ties}"
    return f"{wins}-{losses}"


def render_sidebar() -> tuple[int, int | None]:
    """Returns (selected_season, selected_week)."""
    st.sidebar.title("🏈 Fantasy Dashboard")

    seasons = dd.get_available_seasons()
    if not seasons:
        st.sidebar.error("No data yet - run a refresh.")
        st.stop()

    # Persisted via st.session_state, which (unlike st.query_params) does
    # survive Streamlit's default multipage sidebar navigation - clicking
    # a page link resets the URL's query string, but session_state is
    # tied to the browser session/websocket, not the URL, so it carries
    # over cleanly. Confirmed empirically with a minimal repro before
    # relying on it here.
    if "selected_season" not in st.session_state or st.session_state.selected_season not in seasons:
        st.session_state.selected_season = seasons[0]

    season = st.sidebar.selectbox(
        "Season",
        seasons,
        index=seasons.index(st.session_state.selected_season),
        key="season_selectbox",
    )
    st.session_state.selected_season = season

    meta = dd.get_season_meta(season)
    st.sidebar.markdown(f"**{meta.get('league_name', 'League')}**")
    st.sidebar.caption(f"Season {season} · Week {meta.get('current_week', '?')}")

    latest_week = dd.get_latest_metrics_week(season)
    week = None
    if latest_week:
        state_key = f"selected_week_{season}"
        if state_key not in st.session_state:
            st.session_state[state_key] = latest_week
        week = st.sidebar.slider(
            "Week", min_value=1, max_value=latest_week,
            value=st.session_state[state_key], key=f"week_slider_{season}",
        )
        st.session_state[state_key] = week

    last_refresh = dd.get_last_refresh()
    if last_refresh.get("finished_at"):
        st.sidebar.caption(f"Last refresh: {last_refresh['finished_at']} UTC ({last_refresh['status']})")
    else:
        st.sidebar.caption("No refresh recorded yet")

    if st.sidebar.button("🔄 Refresh ESPN Data", use_container_width=True):
        with st.spinner("Refreshing from ESPN... this can take a few minutes for a full backfill"):
            from .espn_client import ESPNClient
            from .ingest import refresh_all
            from .metrics.pipeline import compute_and_store_all_seasons

            client = ESPNClient()
            status = refresh_all(client=client, seasons=[season])
            conn = dd.get_connection()
            compute_and_store_all_seasons(conn)
            dd.clear_all_caches()
        st.sidebar.success(f"Refresh {status}")
        st.rerun()

    return season, week
