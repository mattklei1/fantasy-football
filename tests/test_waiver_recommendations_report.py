"""Unit tests for fantasy_football.waiver_recommendations_report.build_message
- pure string formatting, no network/DB."""
from fantasy_football.waiver_recommendations_report import build_message


def _suggestion(name="Kirk Cousins", position="QB", bid=25.0, drop="Bench Guy", reasoning="only one bench player at QB"):
    return {
        "player_name": name, "position": position, "pro_team": "ATL", "suggested_bid": bid,
        "suggested_drop": drop, "reasoning": reasoning,
    }


def test_empty_everything_says_so_by_team_name():
    msg = build_message([], [], week=3, budget_remaining=150.0, team_name="McConkey Kong")
    assert "No real free-agent upgrades" in msg
    assert "McConkey Kong" in msg


def test_includes_team_name_budget_and_each_suggestion():
    msg = build_message([_suggestion()], [], week=3, budget_remaining=150.0, team_name="McConkey Kong")
    assert "WEEK 3" in msg
    assert "MCCONKEY KONG" in msg
    assert "$150" in msg
    assert "Kirk Cousins" in msg
    assert "$25" in msg
    assert "only one bench player at QB" in msg
    assert "Drop: Bench Guy" in msg


def test_omits_drop_line_when_no_drop_suggested():
    s = _suggestion(drop=None)
    msg = build_message([s], [], week=3, budget_remaining=150.0, team_name="Team")
    assert "Drop:" not in msg


def test_includes_overlooked_section_with_source():
    overlooked = [{"player_name": "Some Sleeper", "position": "RB", "source": "Yahoo", "suggested_bid": 8.0}]
    msg = build_message([_suggestion()], overlooked, week=3, budget_remaining=150.0, team_name="Team")
    assert "ALSO WORTH A LOOK" in msg
    assert "Some Sleeper" in msg
    assert "via Yahoo" in msg
    assert "$8" in msg


def test_overlooked_without_suggested_bid_omits_dollar_amount():
    overlooked = [{"player_name": "Some Sleeper", "position": "RB", "source": "ESPN", "suggested_bid": None}]
    msg = build_message([], overlooked, week=3, budget_remaining=150.0, team_name="Team")
    assert "Some Sleeper (RB) - via ESPN" in msg
