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
#: separate design language. "warn" (amber, same tone as the
#: SLIGHTLY SUSPICIOUS badge) is the "up in the air" middle ground - a
#: toss-up win probability or a cutline-borderline team, neither clearly
#: good nor bad.
STATUS_COLORS = {"win": "#2e7d32", "loss": "#b71c1c", "neutral": "#6b7280", "warn": "#b8860b"}


def status_bubble_html(label: str, tone: str) -> str:
    """A small colored pill for game status (leading/trailing, favored/
    underdog, won/lost) - tone is one of "win"/"loss"/"neutral"/"warn"."""
    color = STATUS_COLORS.get(tone, STATUS_COLORS["neutral"])
    return f'<span class="ff-badge" style="background:{color}">{label}</span>'


def tone_for_probability(prob: float, warn_band: float = 0.15) -> str:
    """Maps a 0-1 probability (win prob, cutline prob, etc) to a
    "win"/"warn"/"loss" tone - comfortably above 50% is good, comfortably
    below is bad, within warn_band of 50/50 either way is a genuine
    toss-up ("up in the air")."""
    if prob >= 0.5 + warn_band:
        return "win"
    if prob <= 0.5 - warn_band:
        return "loss"
    return "warn"


def team_header_html(name: str, subtext: str) -> str:
    """Compact 2-line team identity block for the Matchups page - `name`
    bold/primary, `subtext` small and muted underneath. Deliberately a
    single small HTML block (not 2 separate st.markdown/st.caption
    calls) to keep each matchup card's vertical footprint tight."""
    return (
        '<div style="line-height:1.3;">'
        f'<div style="font-weight:700; font-size:1.05rem;">{name}</div>'
        f'<div style="font-size:0.78rem; color:var(--text-muted);">{subtext}</div>'
        '</div>'
    )


def score_row_html(actual: str, projected: str | None, tone: str) -> str:
    """Compact actual/projected score line - actual on the left (plain),
    projected larger/bolder and pushed to the right in a tone color
    (win=green/warn=amber/loss=red/neutral=gray) so the live trend reads
    at a glance without a separate FAVORED/UNDERDOG badge. `actual` and
    `projected` are already-formatted strings (caller decides "0.0" vs
    "-" for not-yet-started/unavailable weeks); projected=None omits the
    right-hand side entirely (final week, no projection to show)."""
    color = STATUS_COLORS.get(tone, STATUS_COLORS["neutral"])
    proj_html = (
        f'<span style="font-size:1.15rem; font-weight:700; color:{color};">{projected}</span>'
        if projected is not None else ""
    )
    return (
        '<div style="display:flex; align-items:baseline; justify-content:space-between; '
        'margin-top:4px;">'
        f'<span style="font-size:1.35rem; font-weight:600;">{actual}</span>'
        f'{proj_html}'
        '</div>'
    )


#: Translucent row tint (not a solid fill, so text stays readable in
#: both light and dark theme) for the Median Cutline table - green/safe,
#: amber/toss-up, red/at-risk, same tone language as the score rows.
CUTLINE_TINT = {"win": "rgba(46,125,50,0.15)", "warn": "rgba(184,134,11,0.15)", "loss": "rgba(183,28,28,0.15)"}


def cutline_table_html(rows: list[dict]) -> str:
    """Full HTML table for the Matchups page's Median Cutline widget -
    deliberately NOT st.dataframe: that widget virtualizes/scrolls past a
    fixed height instead of growing to fit every row (all teams need to
    be visible with no scrolling), and its grid renders via canvas so
    individual columns can't be responsively hidden with plain CSS. Each
    row dict needs: rank, team, manager (last name only - caller trims
    it, this function just places it), projected, vs_cutline, make_pct
    (0-100), p10, p90, tone ("win"/"warn"/"loss"). One line per team
    (manager inline to the right of the team name, not stacked
    underneath) keeps row height tight; the manager name is dropped
    entirely below 480px width via a media query so the Team column
    still fits on a phone."""
    body_rows = []
    for r in rows:
        bg = CUTLINE_TINT.get(r["tone"], "transparent")
        body_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:2px 8px; text-align:center;">{r["rank"]}</td>'
            '<td style="padding:2px 8px; white-space:nowrap;">'
            f'<span style="font-weight:600;">{r["team"]}</span>'
            f'<span class="cutline-manager" style="font-size:0.72rem; color:var(--text-muted); '
            f'margin-left:6px;">{r["manager"]}</span>'
            '</td>'
            f'<td style="padding:2px 8px; text-align:right;">{r["projected"]:.1f}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{r["vs_cutline"]:+.1f}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{r["make_pct"]:.0f}%</td>'
            f'<td style="padding:2px 8px; text-align:right;">{r["p10"]:.0f}–{r["p90"]:.0f}</td>'
            '</tr>'
        )
    header = (
        '<tr style="border-bottom:2px solid var(--card-border);">'
        '<th style="padding:2px 8px; text-align:center;">#</th>'
        '<th style="padding:2px 8px; text-align:left;">Team</th>'
        '<th style="padding:2px 8px; text-align:right;">Proj</th>'
        '<th style="padding:2px 8px; text-align:right;">vs Cut</th>'
        '<th style="padding:2px 8px; text-align:right;">Make %</th>'
        '<th style="padding:2px 8px; text-align:right;">10th–90th</th>'
        '</tr>'
    )
    return (
        '<style>@media (max-width: 480px) { .cutline-manager { display: none; } }</style>'
        '<div style="overflow-x:auto;">'
        '<table style="width:100%; border-collapse:collapse; font-size:0.8rem; line-height:1.2;">'
        f'<thead>{header}</thead><tbody>{"".join(body_rows)}</tbody>'
        '</table>'
        '</div>'
    )


def historical_cutline_table_html(rows: list[dict]) -> str:
    """Median Cutline table for an ALREADY-COMPLETED week - same layout/
    styling as cutline_table_html, but for a real, final, deterministic
    result instead of a live in-progress projection (user, 2026-09-16:
    "keep the median cutline for historical weeks" - before this, the
    whole widget just disappeared once a week finished, see pages/1_
    Matchups.py). No Make %/10th-90th range to show (the week's over,
    there's nothing left to model) - a plain Result column instead. Each
    row dict needs: rank, team, manager, score, vs_cutline, made_it
    (bool), tone ("win"/"loss")."""
    body_rows = []
    for r in rows:
        bg = CUTLINE_TINT.get(r["tone"], "transparent")
        result = "Made it" if r["made_it"] else "Missed"
        body_rows.append(
            f'<tr style="background:{bg};">'
            f'<td style="padding:2px 8px; text-align:center;">{r["rank"]}</td>'
            '<td style="padding:2px 8px; white-space:nowrap;">'
            f'<span style="font-weight:600;">{r["team"]}</span>'
            f'<span class="cutline-manager" style="font-size:0.72rem; color:var(--text-muted); '
            f'margin-left:6px;">{r["manager"]}</span>'
            '</td>'
            f'<td style="padding:2px 8px; text-align:right;">{r["score"]:.1f}</td>'
            f'<td style="padding:2px 8px; text-align:right;">{r["vs_cutline"]:+.1f}</td>'
            f'<td style="padding:2px 8px; text-align:right; font-weight:600;">{result}</td>'
            '</tr>'
        )
    header = (
        '<tr style="border-bottom:2px solid var(--card-border);">'
        '<th style="padding:2px 8px; text-align:center;">#</th>'
        '<th style="padding:2px 8px; text-align:left;">Team</th>'
        '<th style="padding:2px 8px; text-align:right;">Score</th>'
        '<th style="padding:2px 8px; text-align:right;">vs Cut</th>'
        '<th style="padding:2px 8px; text-align:right;">Result</th>'
        '</tr>'
    )
    return (
        '<style>@media (max-width: 480px) { .cutline-manager { display: none; } }</style>'
        '<div style="overflow-x:auto;">'
        '<table style="width:100%; border-collapse:collapse; font-size:0.8rem; line-height:1.2;">'
        f'<thead>{header}</thead><tbody>{"".join(body_rows)}</tbody>'
        '</table>'
        '</div>'
    )


def score_detail_html(win_prob_pct: str, range_text: str, tone: str) -> str:
    """Small line under score_row_html's projected number - win
    probability (tone-colored, matching the projected number above it)
    plus the 10th-90th percentile score range, right-aligned so it sits
    directly under the projected column rather than spanning full width."""
    color = STATUS_COLORS.get(tone, STATUS_COLORS["neutral"])
    return (
        '<div style="text-align:right; font-size:0.78rem; margin-top:1px;">'
        f'<span style="color:{color}; font-weight:600;">{win_prob_pct}</span>'
        f'<span style="color:var(--text-muted);"> win · range {range_text}</span>'
        '</div>'
    )


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
    from . import access_control

    explicitly_granted = email in access_control.load_access()
    if email not in config.allowed_emails() and email != admin and not explicitly_granted:
        st.title("🏈 Fantasy Dashboard")
        st.error(
            f"Signed in as {email}, but that address isn't on the league's access list. "
            "Ask the commissioner to add it."
        )
        st.button("Log out", on_click=st.logout)
        st.stop()


def _current_email() -> str | None:
    try:
        if not st.user.is_logged_in:
            return None
    except Exception:
        return None
    return (st.user.email or "").strip().lower() or None


def is_primary_admin() -> bool:
    """True only for config.admin_email() - the ONE original owner of
    this deployment, who can manage OTHER users' league access and the
    registered-leagues list (see access_control.py/league_registry.py).
    Distinct from is_admin() below: every War Room user is "an admin"
    for their own team, but only the primary admin governs who else
    gets that."""
    email = _current_email()
    admin = config.admin_email()
    return bool(email and admin and email == admin)


def is_admin() -> bool:
    """True for the primary admin OR anyone access_control.py has
    explicitly granted war_room access - gates the War Room page
    (pages/9_War_Room.py). Never raises: returns False for any not-
    logged-in/not-configured state, so local dev and everyone else just
    sees the page's locked teaser rather than a crash."""
    email = _current_email()
    if not email:
        return False
    if is_primary_admin():
        return True
    from . import access_control

    return access_control.has_war_room_access(email)


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
    a repeated cost.

    BUG fixed 2026-09-20 (user: "every day that I open the app... it
    says it needs to reboot. Then I need to refresh all of my league
    data from ESPN"): this used to check `seasons` for any row at all,
    but ingest_season() commits ONE season at a time (see ingest.py) -
    the `seasons` row for season N lands (and commits) long before the
    rest of that season's weeks/rosters, and well before any LATER
    season even starts. A cold-start refresh_all() covering 7+ seasons
    of real ESPN calls is exactly the kind of multi-minute operation a
    Streamlit script rerun (triggered by literally any stray widget
    interaction while the spinner is up) can abort partway through -
    which left `seasons` non-empty from whichever seasons happened to
    finish first, so this check silently treated the DB as "already
    bootstrapped" forever after, even with the current season (or most
    of the history) never actually ingested. Nothing in the UI ever
    explained why data was stale/missing - the fix just looked like "I
    have to hit Refresh ESPN Data every time" to the user. Now keyed off
    refresh_log instead, which only gets a row once refresh_all() runs
    to completion (success OR partial-but-finished) - an interrupted run
    leaves it empty, so the NEXT page load correctly retries the whole
    bootstrap instead of getting stuck half-populated."""
    conn = dd.get_connection()
    db.init_db(conn)
    has_completed_refresh = conn.execute("SELECT COUNT(*) FROM refresh_log").fetchone()[0] > 0
    if has_completed_refresh:
        return

    from .ingest import refresh_all
    from .league_context import get_active_espn_client
    from .metrics.pipeline import compute_and_store_all_seasons

    with st.spinner(
        "First load after a restart - rebuilding league history from ESPN "
        "(a few minutes, only happens once per restart)..."
    ):
        client = get_active_espn_client()
        refresh_all(client=client)
        compute_and_store_all_seasons(conn)
        dd.clear_all_caches()


def ensure_daily_data_fresh() -> None:
    """Companion to ensure_data_bootstrapped(): keeps all 3 of Roster
    Strength's inputs (FantasyPros ROS rank, ESPN season-to-date
    positional rank, ESPN weekly point projection) AND team metadata
    (name, record - e.g. a manager renaming their team in ESPN) current
    on the LIVE deployed site without a manual "Refresh ESPN Data" click.
    GitHub Actions can't reach this site's local DB directly (see
    ensure_data_bootstrapped's docstring on filesystem persistence), so
    a daily refresh has to be triggered from inside the app itself -
    here, on page load. The should_refresh_daily gate (see
    schedule_guard) makes this a cheap no-op after the first page load
    each day; only a genuinely due refresh pays the cost of an ESPN +
    FantasyPros call."""
    conn = dd.get_connection()
    seasons = dd.get_available_seasons()
    if not seasons:
        return
    season = seasons[0]
    last_rs_refresh = db.get_roster_strength_last_refreshed(conn, season)
    from .schedule_guard import should_refresh_daily

    if not should_refresh_daily(last_rs_refresh):
        return

    from .ingest import refresh_daily_data_if_due
    from .league_context import get_active_espn_client

    with st.spinner("Refreshing today's team info and Roster Strength rankings..."):
        client = get_active_espn_client()
        league = client.get_league(season)
        refresh_daily_data_if_due(conn, league, season, log=lambda *a: None)
        dd.clear_all_caches()


def set_flash(key: str, level: str, message: str) -> None:
    """Stashes a status message in session_state to survive the
    st.rerun() that commonly follows a save/clear action - calling
    st.success()/st.warning() right before st.rerun() never reaches the
    user, since the rerun wipes it before it renders (a real bug found
    live 2026-09-15: a PDF override that failed to commit to git showed
    no warning at all). show_flash() displays it once on the next run,
    then clears it so it doesn't linger on later reruns."""
    st.session_state[f"_flash_{key}"] = (level, message)


def show_flash(key: str) -> None:
    flash = st.session_state.pop(f"_flash_{key}", None)
    if flash:
        level, message = flash
        getattr(st, level)(message)


def render_league_selector() -> None:
    """"Which league is this" sidebar picker - visible to the primary
    admin (sees every registered league) AND to any signed-in visitor
    access_control.py has explicitly granted 2+ leagues to ("overlap
    managers... should see the league selector option"). Anyone with
    only ONE accessible league (the common case) sees no selector at
    all - nothing to pick. Every visitor still gets league_context.
    sync_db_path() called unconditionally first (cheap - just points
    db.DB_PATH at whichever league is active for THIS session, primary
    for everyone but a multi-league user who's picked something else),
    so a regular single-league member's session is completely
    unaffected regardless of what anyone else is browsing in their own
    session - Streamlit session_state is per-browser-session, never
    shared. "Manage leagues"/"Manage league access" stay primary-admin-
    only - a multi-league user can switch between THEIR OWN leagues,
    not register new ones or grant access to others."""
    from . import access_control, league_context, league_registry

    league_context.sync_db_path()

    primary_id = config.load_espn_credentials().league_id
    registered = league_registry.load_registered_leagues()
    email = _current_email()

    if is_primary_admin():
        accessible_ids = [primary_id] + sorted(registered.keys())
    elif email:
        granted = access_control.get_user_leagues(email, primary_id)
        accessible_ids = [primary_id] + sorted(registered.keys()) if granted == "all" else granted
    else:
        accessible_ids = [primary_id]

    labels = {primary_id: "Salted by Quincy"}
    for lid, info in registered.items():
        labels[lid] = info.get("name") or f"League {lid}"

    if len(accessible_ids) > 1:
        current = league_context.get_active_league_id()
        if current not in accessible_ids:
            current = primary_id
        choice = st.sidebar.selectbox(
            "League", accessible_ids, index=accessible_ids.index(current),
            format_func=lambda lid: labels.get(lid, str(lid)), key="league_selector",
        )
        if choice != current:
            league_context.set_active_league_id(choice)
            league_context.sync_db_path()
            dd.clear_all_caches()
            st.rerun()


def render_manage_users_tab() -> None:
    """The ONE SOURCE OF TRUTH for "who has what access to which
    leagues" - registered leagues, every user's league/War Room grants,
    and whether they have their own ESPN credentials configured, all in
    one place instead of scattered across a sidebar expander, a
    Streamlit secrets table, and an env var (user feedback 2026-09-15:
    "I forget all the places I need to add them"). Primary-admin-only -
    call from inside an `if ui.is_primary_admin():` guarded tab (see
    pages/9_War_Room.py)."""
    from . import access_control, league_context, league_registry

    primary_id = config.load_espn_credentials().league_id
    registered = league_registry.load_registered_leagues()
    labels = {primary_id: "Salted by Quincy"}
    for lid, info in registered.items():
        labels[lid] = info.get("name") or f"League {lid}"

    st.markdown("#### Registered leagues")
    show_flash("league_registry")
    if registered:
        st.dataframe(
            [{"League ID": lid, "Name": info.get("name"), "Season": info.get("season")} for lid, info in registered.items()],
            hide_index=True, use_container_width=True,
        )
    else:
        st.caption("No extra leagues registered yet - just the primary one.")

    col_add, col_remove = st.columns(2)
    with col_add:
        new_id_raw = st.text_input("Add league by ESPN league ID", key="league_registry_add_id")
        if st.button("Add league", key="league_registry_add_btn") and new_id_raw.strip():
            try:
                new_id = int(new_id_raw.strip())
            except ValueError:
                st.error("League ID must be numeric.")
            else:
                try:
                    from .espn_client import ESPNClient

                    primary = config.load_espn_credentials()
                    test_client = ESPNClient(
                        credentials=config.ESPNCredentials(
                            league_id=new_id, espn_s2=primary.espn_s2, swid=primary.swid,
                            current_season=primary.current_season,
                        )
                    )
                    league = test_client.get_league(primary.current_season)
                    save_result = league_registry.add_league(new_id, league.settings.name, primary.current_season)
                    level = "success" if save_result["committed"] else "warning"
                    prefix = f"Added \"{league.settings.name}\" ({new_id})."
                    set_flash(
                        "league_registry", level,
                        prefix if save_result["committed"] else f"{prefix} {save_result['commit_message']}",
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001 - a bad league id/no access should show a clear error, not crash
                    st.error(f"Couldn't add league {new_id}: {type(exc).__name__}: {exc}")
    with col_remove:
        if registered:
            remove_id = st.selectbox(
                "Remove a league", list(registered.keys()),
                format_func=lambda lid: labels.get(lid, str(lid)), key="league_registry_remove_id",
            )
            if st.button("Remove", key="league_registry_remove_btn"):
                save_result = league_registry.remove_league(remove_id)
                league_context.sync_db_path()
                if not save_result["committed"]:
                    set_flash("league_registry", "warning", save_result["commit_message"])
                st.rerun()

    st.divider()
    st.markdown("#### Who has access to what")
    show_flash("league_access")

    admin_email = config.admin_email()
    access = access_control.load_access()
    rows = []
    if admin_email:
        rows.append(
            {
                "Email": admin_email, "Leagues": "All (primary admin)", "War Room": True,
                "Own ESPN credentials": bool(config.get_manager_credentials(admin_email)),
            }
        )
    for email, entry in access.items():
        league_names = ", ".join(labels.get(lid, str(lid)) for lid in entry["leagues"]) or "(primary only)"
        has_extra_league = any(lid != primary_id for lid in entry["leagues"])
        has_creds = bool(config.get_manager_credentials(email))
        row = {
            "Email": email, "Leagues": league_names, "War Room": entry["war_room"],
            "Own ESPN credentials": has_creds,
        }
        rows.append(row)
        if entry["war_room"] and has_extra_league and not has_creds:
            st.warning(
                f"**{email}** has War Room access to a non-primary league but no ESPN credentials "
                "configured - they'll see the PRIMARY account's team there, not their own, until "
                "credentials are added (see below)."
            )

    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.caption("Nobody's been granted anything yet - everyone but the primary admin sees just the primary league.")

    st.caption(
        "Site login: anyone granted below can log in even if they're not separately on the "
        "ALLOWED_EMAILS secret. Own ESPN credentials aren't settable here (a real login credential, "
        "same sensitivity as the primary account's own ESPN_S2/SWID) - add them as a Streamlit Cloud "
        "secret: `[manager_credentials.\"their-email@example.com\"]` with `espn_s2`/`swid` keys, from "
        "their own browser session. Without their own credentials, War Room's \"my team\" and any real "
        "write (waiver claims, lineup submits) uses the PRIMARY account's team instead of theirs."
    )

    grant_email = st.text_input("Email to grant/edit", key="league_access_email")
    grant_league_ids = st.multiselect(
        "Extra leagues", sorted(registered.keys()), format_func=lambda lid: labels.get(lid, str(lid)),
        key="league_access_leagues",
    )
    grant_war_room = st.checkbox("War Room access", key="league_access_war_room")
    col_grant, col_revoke = st.columns(2)
    with col_grant:
        if st.button("Save grant", key="league_access_save") and grant_email.strip():
            save_result = access_control.set_user_access(grant_email, grant_league_ids, grant_war_room)
            level = "success" if save_result["committed"] else "warning"
            set_flash(
                "league_access", level,
                "Saved." if save_result["committed"] else f"Saved locally only. {save_result['commit_message']}",
            )
            st.rerun()
    with col_revoke:
        if st.button("Revoke", key="league_access_revoke") and grant_email.strip():
            save_result = access_control.remove_user_access(grant_email)
            if not save_result["committed"]:
                set_flash("league_access", "warning", save_result["commit_message"])
            st.rerun()


def render_sidebar(support_all_time: bool = False) -> tuple[int | str, int | None]:
    """Returns (selected_season, selected_week). `selected_season` is
    normally a real season int; pass support_all_time=True (History,
    Lineup Efficiency) to let it also come back as the literal string
    "All time" when the visitor picks that from the Season selector -
    every other page doesn't support an all-time view, so picking it
    there silently falls back to the current season instead (see the
    reset logic below)."""
    require_password()
    require_login()
    render_league_selector()
    ensure_data_bootstrapped()
    ensure_daily_data_fresh()

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

    # "All time" is only a real value on pages that opt in
    # (support_all_time=True) - it's never written to
    # session_state.selected_season itself (that stays a real season int
    # always), so pages that don't support it are unaffected.
    ALL_TIME = "All time"
    options = [ALL_TIME] + seasons

    # A key'd widget's session_state value persists across pages and
    # overrides the `index` param on every render after the first mount -
    # so landing on a page that does NOT support "All time" while the
    # widget's own state still says "All time" (carried over from a page
    # that does) needs an explicit correction, not just a different
    # `index`. Can't write to the widget's key in the SAME run we detect
    # the mismatch - the widget's already been instantiated by then, and
    # Streamlit raises StreamlitWidgetAlreadyInstantiatedError for
    # writing to its key after creation (hit this in production,
    # 2026-09-13) - so the correction happens at the top of the NEXT run,
    # forced immediately via st.rerun() rather than waiting on the user
    # to interact with something else first.
    if not support_all_time and st.session_state.get("_reset_season_selectbox"):
        st.session_state["season_selectbox"] = st.session_state.selected_season
        st.session_state["_reset_season_selectbox"] = False

    season_choice = st.sidebar.selectbox(
        "Season",
        options,
        index=options.index(st.session_state.selected_season),
        key="season_selectbox",
    )

    if season_choice == ALL_TIME and not support_all_time:
        st.session_state["_reset_season_selectbox"] = True
        st.rerun()

    if season_choice == ALL_TIME:
        season = ALL_TIME
    else:
        season = season_choice
        st.session_state.selected_season = season

    week = None
    if season == ALL_TIME:
        st.sidebar.caption("Every season combined")
    else:
        meta = dd.get_season_meta(season)
        st.sidebar.markdown(f"**{meta.get('league_name', 'League')}**")
        st.sidebar.caption(f"Season {season} · Week {meta.get('current_week', '?')}")

        latest_week = dd.get_latest_metrics_week(season)
        if latest_week:
            from . import league_context

            # Keyed by (league, season), not season alone - two leagues can
            # share a season but have different latest computed weeks, and
            # a keyed widget's OWN stored value overrides `value=` on every
            # render after the first mount, so a stale value from a
            # previously active league with a HIGHER latest_week crashes
            # st.sidebar.slider's min/max check the moment the active
            # league changes underneath it (hit in production 2026-09-15,
            # right after multi-league support shipped: switching leagues
            # left the old, larger week number stored under a key the new
            # league's smaller max_value couldn't satisfy).
            active_league_id = league_context.get_active_league_id()
            state_key = f"selected_week_{active_league_id}_{season}"
            if latest_week == 1:
                # st.slider raises StreamlitInvalidMinMaxError when
                # min_value == max_value - a brand-new league with only
                # Week 1 computed has nothing to pick between, so just
                # show it rather than rendering a (broken) slider.
                week = 1
                st.sidebar.caption("Week 1 (only week available so far)")
            else:
                if state_key not in st.session_state:
                    st.session_state[state_key] = latest_week
                else:
                    st.session_state[state_key] = max(1, min(st.session_state[state_key], latest_week))
                week = st.sidebar.slider(
                    "Week", min_value=1, max_value=latest_week,
                    value=st.session_state[state_key], key=f"week_slider_{active_league_id}_{season}",
                )
            st.session_state[state_key] = week

    last_refresh = dd.get_last_refresh()
    if last_refresh.get("finished_at"):
        st.sidebar.caption(f"Last refresh: {last_refresh['finished_at']} UTC ({last_refresh['status']})")
    else:
        st.sidebar.caption("No refresh recorded yet")

    refresh_label = "🔄 Refresh ESPN Data" if season != ALL_TIME else "🔄 Refresh ESPN Data (all seasons)"
    if st.sidebar.button(refresh_label, use_container_width=True):
        with st.spinner("Refreshing from ESPN... this can take a few minutes for a full backfill"):
            from .ingest import refresh_all
            from .league_context import get_active_espn_client
            from .metrics.pipeline import compute_and_store_all_seasons

            client = get_active_espn_client()
            status = refresh_all(client=client, seasons=None if season == ALL_TIME else [season])
            conn = dd.get_connection()
            compute_and_store_all_seasons(conn)
            dd.clear_all_caches()
        st.sidebar.success(f"Refresh {status}")
        st.rerun()

    return season, week
