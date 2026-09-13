"""ASK ME ANYTHING page: natural-language Q&A over league history/
rosters/stats, powered by Gemini Flash. See fantasy_football/ama.py
(orchestration) and fantasy_football/ama_query.py (the actual security
boundary - FantasyPros rankings are structurally unreachable here, not
just asked-nicely-not-to)."""
from __future__ import annotations

import time

import streamlit as st

from fantasy_football import ama, ama_query, config
from fantasy_football import dashboard_data as dd
from fantasy_football import ui_common as ui

st.set_page_config(page_title="Ask Me Anything", page_icon="🔮", layout="wide")
ui.inject_css()

season, _ = ui.render_sidebar()

st.title("Ask Me Anything")
st.caption(
    "Ask anything about league history, standings, rosters, or past weeks - answered by Gemini "
    "from this dashboard's own data. It cannot see third-party rankings data or any credentials."
)

api_key = config.gemini_api_key()
if not api_key:
    st.info(
        "Ask Me Anything isn't configured yet - set `GEMINI_API_KEY` in `.env` to enable it "
        "(get a key at https://aistudio.google.com/apikey). No other page requires this."
    )
    st.stop()

teams = dd.get_standings(season)
team_options = ["Just browsing"] + (
    teams["manager_name"].dropna().unique().tolist() if not teams.empty else []
)
asking_as = st.selectbox(
    "Ask as",
    team_options,
    help="No login on this app - pick your name so questions like \"my roster\" or \"my chances\" "
    "resolve to the right team. Anyone can pick anyone (same as browsing the rest of this dashboard).",
)

# Simple per-browser-session rate limit - keeps Gemini spend bounded once
# this is reachable by the whole league, without needing real accounts/
# server-side tracking for a private hobby-league tool.
RATE_LIMIT_QUESTIONS = 20
RATE_LIMIT_WINDOW_SECONDS = 3600
if "ama_question_times" not in st.session_state:
    st.session_state.ama_question_times = []

question = st.text_input(
    "Your question",
    placeholder="e.g. Who has the most championships? What's my roster look like? Who's the unluckiest manager ever?",
)
ask_clicked = st.button("Ask", type="primary")

if ask_clicked and question.strip():
    now = time.time()
    st.session_state.ama_question_times = [
        t for t in st.session_state.ama_question_times if now - t < RATE_LIMIT_WINDOW_SECONDS
    ]
    if len(st.session_state.ama_question_times) >= RATE_LIMIT_QUESTIONS:
        st.warning(
            f"That's {RATE_LIMIT_QUESTIONS} questions in the last hour from this browser - "
            "give it a bit and try again."
        )
    else:
        st.session_state.ama_question_times.append(now)
        context = {"current_dashboard_season": season, "asking_as_manager": asking_as}
        with st.spinner("Thinking..."):
            try:
                result = ama.ask(config.DB_PATH, question, context, api_key, config.gemini_model())
            except ama_query.AmaQueryError as exc:
                result = None
                st.warning(str(exc))
            except Exception as exc:  # noqa: BLE001 - any Gemini/SDK failure, never crash the page
                result = None
                st.error(f"Couldn't get an answer right now ({exc.__class__.__name__}). Try rephrasing?")

        if result:
            st.markdown(result["answer"])
            with st.expander("How this was answered"):
                st.caption(f"{result['row_count']} row(s) retrieved. SQL used:")
                st.code(result["sql"], language="sql")

with st.expander("What can I ask?"):
    st.markdown(
        """
- **Standings & records**: "Who's in first place?", "What's the all-time head-to-head between
  X and Y?", "Who has the most championships?"
- **Luck & fraud**: "Who's the luckiest manager this season?", "Who's the biggest fraud?"
- **Lineup decisions**: "Who's left the most points on their bench?", "What's my decision accuracy?"
- **Rosters & drafts**: "Who did I draft in the first round?", "What's on my bench right now?"
- **Weekly recaps**: "What happened in week 5?", "Summarize the closest game this season."

**Not available**: third-party rest-of-season rankings (kept private on purpose), and anything not
already tracked elsewhere in this dashboard - Ask Me Anything answers from the same data you can
already see on the other pages, it doesn't have outside information.
        """
    )
