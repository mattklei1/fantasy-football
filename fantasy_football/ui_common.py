"""Shared Streamlit presentation helpers: theme CSS, sidebar (league info,
season switcher, refresh button), and small formatting utilities reused
across Home.py and pages/*.py."""
from __future__ import annotations

import streamlit as st

from . import config, dashboard_data as dd, db
from .badges import fraud_badge

BADGE_COLORS = {
    "LEGIT": "#2e7d32",
    "SLIGHTLY SUSPICIOUS": "#b8860b",
    "FRAUD WATCH": "#c9622a",
    "GENERATIONAL FRAUD": "#b71c1c",
    "SLIGHTLY UNLUCKY": "#5b7fb5",
    "UNLUCKY": "#3454a0",
    "SNAKEBIT": "#1e3a8a",
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
    /* Stat cards often sit stacked 2-per-column next to other columns'
       stacked cards (e.g. History's League Records grid) - a longer
       subtext wrapping to 2 lines on one card was pushing everything
       below it in that column out of alignment with neighboring
       columns. A shared min-height keeps every card the same height
       regardless of how much its subtext wraps. */
    min-height: 96px;
    box-sizing: border-box;
}
.ff-card-label {
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-muted);
    font-weight: 600;
    margin-bottom: 4px;
}
.ff-card-icon {
    font-size: 1.1rem;
    margin-right: 6px;
    letter-spacing: normal;
    text-transform: none;
}
.ff-card {
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.ff-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.08);
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


def stat_card(label: str, value: str, sub: str | None = None, icon: str | None = None, accent: str | None = None) -> None:
    sub_html = f'<div class="ff-card-sub">{sub}</div>' if sub else ""
    icon_html = f'<span class="ff-card-icon">{icon}</span>' if icon else ""
    border_style = f' style="border-left: 4px solid {accent};"' if accent else ""
    st.markdown(
        f'<div class="ff-card"{border_style}><div class="ff-card-label">{icon_html}{label}</div>'
        f'<div class="ff-card-value">{value}</div>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def badge_html(label: str) -> str:
    color = BADGE_COLORS.get(label, "#6b7280")
    return f'<span class="ff-badge" style="background:{color}">{label}</span>'


def fraud_badge_html(fraud_index: float | None) -> str:
    return badge_html(fraud_badge(fraud_index))


#: Eye-friendly (not pure-saturated) green/red for the Matchups page's
#: win/loss "bubbles" - same muted palette as BADGE_COLORS above, not a
#: separate design language.
STATUS_COLORS = {"win": "#2e7d32", "loss": "#b71c1c", "neutral": "#6b7280"}


def status_bubble_html(label: str, tone: str) -> str:
    """A small colored pill for game status (leading/trailing, favored/
    underdog, won/lost) - tone is one of "win"/"loss"/"neutral"."""
    color = STATUS_COLORS.get(tone, STATUS_COLORS["neutral"])
    return f'<span class="ff-badge" style="background:{color}">{label}</span>'


def trend_arrow(change) -> str:
    """Plain-text (no HTML) rank-trend arrow - for st.dataframe/column_config
    cells, which render HTML tags as literal text rather than parsing them
    (unlike the old to_html()-table approach, hence plain glyphs + a
    separate trend_color() for pandas Styler instead of inline HTML)."""
    if change is None or (isinstance(change, float) and change != change):  # NaN
        return "–"
    change = int(change)
    if change > 0:
        return f"▲{change}"
    if change < 0:
        return f"▼{abs(change)}"
    return "–"


def trend_color(value: str) -> str:
    """pandas Styler CSS string for a trend_arrow() cell - green/red/gray,
    same palette as the rest of the app's badges."""
    if isinstance(value, str) and value.startswith("▲"):
        return "color: #2e7d32; font-weight: 700;"
    if isinstance(value, str) and value.startswith("▼"):
        return "color: #b71c1c; font-weight: 700;"
    return "color: #6b7280;"


def format_record(wins, losses, ties=0) -> str:
    wins, losses, ties = int(wins or 0), int(losses or 0), int(ties or 0)
    if ties:
        return f"{wins}-{losses}-{ties}"
    return f"{wins}-{losses}"


def require_password() -> None:
    """Optional shared-password gate for a hosted deployment reachable by
    more than just the developer - a deliberately simple, no-accounts
    access control for a small private league (see config.app_password()),
    not a replacement for real per-user auth. No-ops entirely when
    APP_PASSWORD isn't set, so local dev never sees a password prompt.
    Called from render_sidebar() (every page goes through it) rather than
    needing to be pasted into every pages/*.py file individually."""
    password = config.app_password()
    if not password or st.session_state.get("authenticated"):
        return

    st.title("🏈 Fantasy Dashboard")
    entered = st.text_input("Password", type="password", key="app_password_input")
    if st.button("Enter"):
        if entered == password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()


def require_login() -> None:
    """Real per-user auth via Streamlit's native st.login() (Google OAuth) -
    each league member signs in with their own Google account instead of
    a password everyone shares. No-ops entirely when the deployment's
    secrets.toml has no [auth] section configured, so local dev (no
    Google OAuth app set up) never sees a login wall - same pattern as
    require_password() above. Deliberately fails CLOSED on authorization:
    a real, successful Google sign-in that isn't on config.allowed_emails()
    is still turned away, since this gates a private, real-money league,
    not "any Google user may enter." Called from render_sidebar()
    alongside require_password() - both gates enforce independently, so
    either can be configured/removed without touching the other."""
    try:
        auth_configured = bool(st.secrets.get("auth"))
    except Exception:
        auth_configured = False
    if not auth_configured:
        return

    if not st.user.is_logged_in:
        st.title("🏈 Fantasy Dashboard")
        st.write("Sign in with the Google account your commissioner put on the access list.")
        st.button("Log in with Google", on_click=st.login)
        st.stop()

    email = (st.user.email or "").strip().lower()
    admin = config.admin_email()
    if email not in config.allowed_emails() and email != admin:
        st.title("🏈 Fantasy Dashboard")
        st.error(
            f"Signed in as {email}, but that address isn't on the league's access list. "
            "Ask the commissioner to add it."
        )
        st.button("Log out", on_click=st.logout)
        st.stop()


def is_admin() -> bool:
    """True only when the signed-in visitor is config.admin_email() -
    gates the commissioner-only War Room page (pages/9_War_Room.py).
    Never raises: returns False for any not-logged-in/not-configured
    state, so local dev and everyone else just sees the page's locked
    teaser rather than a crash."""
    try:
        if not st.user.is_logged_in:
            return False
    except Exception:
        return False
    admin = config.admin_email()
    if not admin:
        return False
    return (st.user.email or "").strip().lower() == admin


def ensure_data_bootstrapped() -> None:
    """Streamlit Community Cloud's local filesystem isn't reliably
    persistent - a redeploy, and possibly the 12-hour idle sleep/wake
    cycle, can wipe data/league.db (confirmed via Streamlit's own
    community forum, not assumed). Rather than surface a confusing "no
    data" dead end to a league member who just wanted to check
    standings, detect an empty DB and transparently rebuild it from ESPN
    before rendering anything - the same full-history refresh the manual
    "Refresh ESPN Data" button already runs, just triggered
    automatically on the first page load after a restart. The presence
    check itself is one cheap COUNT query on every other page load, not
    a repeated cost."""
    conn = dd.get_connection()
    db.init_db(conn)
    has_data = conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0] > 0
    if has_data:
        return

    from .espn_client import ESPNClient
    from .ingest import refresh_all
    from .metrics.pipeline import compute_and_store_all_seasons

    with st.spinner(
        "First load after a restart - rebuilding league history from ESPN "
        "(a few minutes, only happens once per restart)..."
    ):
        client = ESPNClient()
        refresh_all(client=client)
        compute_and_store_all_seasons(conn)
        dd.clear_all_caches()


def render_sidebar() -> tuple[int, int | None]:
    """Returns (selected_season, selected_week)."""
    require_password()
    require_login()
    ensure_data_bootstrapped()

    st.sidebar.title("🏈 Fantasy Dashboard")

    try:
        logged_in = st.user.is_logged_in
    except Exception:
        logged_in = False
    if logged_in:
        st.sidebar.caption(f"Signed in as {st.user.email}")
        st.sidebar.button("Log out", on_click=st.logout, key="sidebar_logout")

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

    # "All time" is a one-shot shortcut to the History page, NOT a real
    # selectable value - it's never written to session_state.selected_season
    # (that stays a real season int always), so every other page's season
    # handling is unaffected. Picking it jumps straight to History, which
    # already covers all seasons on its own and ignores this selector.
    ALL_TIME = "All time"
    options = seasons + [ALL_TIME]
    season_choice = st.sidebar.selectbox(
        "Season",
        options,
        index=options.index(st.session_state.selected_season),
        key="season_selectbox",
    )
    if season_choice == ALL_TIME:
        # Streamlit widgets with a `key` persist their OWN value in
        # session_state and that persisted value overrides the `index`
        # param on every future render (index only seeds the very first
        # mount) - without this reset, the selectbox stayed stuck showing
        # "All time" forever on every subsequent page, silently feeding
        # the literal string "All time" into every page's `season`
        # variable instead of a real season int (broke Lineup Efficiency
        # and anything else that actually uses it - found 2026-09-13).
        st.session_state["season_selectbox"] = st.session_state.selected_season
        st.switch_page("pages/5_History.py")
    season = season_choice
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
