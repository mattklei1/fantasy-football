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


def test_build_message_sorts_closest_games_first():
    matchups = [
        MatchupSnapshot("Blowout A", 100.0, "Blowout B", 20.0),
        MatchupSnapshot("Close A", 50.0, "Close B", 51.0),
        MatchupSnapshot("Mid A", 60.0, "Mid B", 45.0),
    ]
    msg = build_message("Early slate update", matchups, [])
    close_idx = msg.index("Close A")
    mid_idx = msg.index("Mid A")
    blowout_idx = msg.index("Blowout A")
    assert close_idx < mid_idx < blowout_idx


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
