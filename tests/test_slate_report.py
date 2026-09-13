"""Unit tests for fantasy_football.slate_report - pure formatting logic
only (fetch_live_matchups/top_individual_scores need real espn_api
BoxScore objects, validated live instead - see PROJECT_BRIEF)."""
from fantasy_football.slate_report import MatchupSnapshot, build_message


def test_margin_is_absolute():
    m = MatchupSnapshot("A", 50.0, "B", 80.0)
    assert m.margin == 30.0
    m2 = MatchupSnapshot("A", 80.0, "B", 50.0)
    assert m2.margin == 30.0


def test_build_message_empty_matchups():
    msg = build_message("Early slate update", [], [])
    assert "no games in progress" in msg.lower()


def test_build_message_sorts_by_projected_margin_not_raw_score_margin():
    # The real bug being fixed: a huge score-so-far gap where the trailing
    # team simply hasn't played yet should NOT be treated as "not close" -
    # projected finish is what actually matters.
    matchups = [
        MatchupSnapshot("Real Rout A", 100.0, "Real Rout B", 20.0, home_projected=105.0, away_projected=25.0),
        MatchupSnapshot("Not Actually Close A", 60.0, "Not Actually Close B", 5.0, home_projected=95.0, away_projected=90.0),
        MatchupSnapshot("Middling A", 60.0, "Middling B", 45.0, home_projected=90.0, away_projected=70.0),
    ]
    msg = build_message("Early slate update", matchups, [])
    not_actually_close_idx = msg.index("Not Actually Close A")
    middling_idx = msg.index("Middling A")
    real_rout_idx = msg.index("Real Rout A")
    # raw score margins would rank Middling(15) < Real Rout(80) < Not Actually Close(55);
    # projected margins correctly rank Not Actually Close(5) < Middling(20) < Real Rout(80)
    assert not_actually_close_idx < middling_idx < real_rout_idx


def test_matchup_snapshot_str_includes_projection_when_present():
    m = MatchupSnapshot("A", 10.0, "B", 5.0, home_projected=95.0, away_projected=90.0)
    text = str(m)
    assert "95.0" in text and "90.0" in text


def test_matchup_snapshot_str_omits_projection_when_absent():
    m = MatchupSnapshot("A", 10.0, "B", 5.0)
    assert "proj" not in str(m)


def test_projected_margin_is_absolute():
    m = MatchupSnapshot("A", 10.0, "B", 5.0, home_projected=80.0, away_projected=100.0)
    assert m.projected_margin == 20.0


def test_build_message_includes_top_scores():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    top = [("Star Player", "Team A", 35.2)]
    msg = build_message("Early slate update", matchups, top)
    assert "Star Player" in msg
    assert "35.2" in msg


def test_build_message_no_markdown_leaks_in():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    msg = build_message("Early slate update", matchups, [("P", "T", 1.0)])
    assert "**" not in msg
