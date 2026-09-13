"""Unit tests for fantasy_football.waiver_report. No live ESPN/
FantasyPros calls - fetch_week_claims is tested against a small fake
`league` object shaped like espn_api's real Transaction/TransactionItem
classes; build_message is tested directly against constructed
PlayerClaimResult objects (per PROJECT_BRIEF testing requirements)."""
from types import SimpleNamespace

import pytest

from fantasy_football.waiver_report import PlayerClaimResult, build_message, fetch_week_claims


def _team(name):
    return SimpleNamespace(team_name=name)


def _item(player_id, player_name, item_type="ADD"):
    return SimpleNamespace(type=item_type, playerId=player_id, player=player_name)


def _txn(team, status, bid_amount, items):
    return SimpleNamespace(team=team, status=status, bid_amount=bid_amount, items=items)


class _FakeLeague:
    def __init__(self, txns):
        self._txns = txns

    def transactions(self, scoring_period=None, types=None):
        return self._txns


def test_fetch_week_claims_groups_by_player_and_finds_winner():
    txns = [
        _txn(_team("Team A"), "EXECUTED", 12, [_item(100, "Daniel Jones")]),
        _txn(_team("Team B"), "FAILED_INVALIDPLAYERSOURCE", 5, [_item(100, "Daniel Jones")]),
        _txn(_team("Team C"), "CANCELED", 3, [_item(100, "Daniel Jones")]),
    ]
    claims = fetch_week_claims(_FakeLeague(txns), scoring_period=1)
    assert len(claims) == 1
    c = claims[0]
    assert c.player_name == "Daniel Jones"
    assert c.winner_team == "Team A"
    assert c.winning_bid == 12
    assert c.contested is True
    assert len(c.all_bids) == 3


def test_fetch_week_claims_ignores_drop_items():
    txns = [_txn(_team("Team A"), "EXECUTED", 5, [_item(1, "Player X"), _item(2, "Player Y", "DROP")])]
    claims = fetch_week_claims(_FakeLeague(txns), scoring_period=1)
    assert len(claims) == 1
    assert claims[0].player_name == "Player X"


def test_fetch_week_claims_zero_bid_free_agent_not_marked_contested():
    txns = [_txn(_team("Team A"), "EXECUTED", 0, [_item(1, "Player X")])]
    claims = fetch_week_claims(_FakeLeague(txns), scoring_period=1)
    assert claims[0].contested is False
    assert claims[0].all_bids == []


def test_fetch_week_claims_no_winner_when_all_failed():
    txns = [_txn(_team("Team A"), "CANCELED", 5, [_item(1, "Player X")])]
    claims = fetch_week_claims(_FakeLeague(txns), scoring_period=1)
    assert claims[0].winner_team is None
    assert claims[0].winning_bid is None


def test_overspent_requires_both_ratio_and_dollar_floor():
    # ratio triggers (2x) but under the $5 floor - should NOT flag
    small = PlayerClaimResult(1, "Cheap Guy", "RB", "Team A", 2.0, suggested_bid=1.0)
    assert small.overspent is False

    # both ratio and floor cleared - should flag
    big = PlayerClaimResult(2, "Expensive Guy", "RB", "Team A", 30.0, suggested_bid=10.0)
    assert big.overspent is True

    # under the ratio even with a big dollar gap - should NOT flag
    ratio_ok = PlayerClaimResult(3, "Fine Guy", "RB", "Team A", 20.0, suggested_bid=15.0)
    assert ratio_ok.overspent is False


def test_steal_requires_both_ratio_and_dollar_floor():
    # ratio triggers (bid is half of suggested) but under the $5 floor - should NOT flag
    small = PlayerClaimResult(1, "Cheap Guy", "RB", "Team A", 4.0, suggested_bid=8.0)
    assert small.steal is False

    # both ratio and floor cleared - should flag
    big = PlayerClaimResult(2, "Bargain Guy", "RB", "Team A", 3.0, suggested_bid=20.0)
    assert big.steal is True

    # under the ratio (more than half of suggested) even with a big dollar gap - should NOT flag
    ratio_ok = PlayerClaimResult(3, "Fine Guy", "RB", "Team A", 15.0, suggested_bid=20.0)
    assert ratio_ok.steal is False


def test_overspent_and_steal_are_mutually_exclusive():
    # sanity check: no single bid can be both an overpay and a steal
    for winning_bid, suggested in [(30.0, 10.0), (2.0, 10.0), (10.0, 10.0)]:
        c = PlayerClaimResult(1, "P", "RB", "Team A", winning_bid, suggested_bid=suggested)
        assert not (c.overspent and c.steal)


def test_build_message_empty_week():
    msg = build_message([], week=3)
    assert "quiet week" in msg.lower()


def test_build_message_highlights_contested_and_overspent():
    contested = PlayerClaimResult(
        1, "Daniel Jones", "QB", "Doody Guac Boys", 12.0,
        all_bids=[("Doody Guac Boys", 12.0), ("Team B", 5.0), ("Team C", 2.0)], suggested_bid=8.0,
    )
    overspent = PlayerClaimResult(2, "Panic Pickup", "RB", "Hammer Time", 40.0, suggested_bid=5.0)
    bargain = PlayerClaimResult(3, "Bargain Bin Bijan", "RB", "Roses to Flowers", 2.0, suggested_bid=25.0)
    quiet = PlayerClaimResult(4, "Nobody Cares", "K", "Roses to Flowers", 1.0, suggested_bid=1.0)

    msg = build_message([contested, overspent, bargain, quiet], week=5)

    assert "WEEK 5 WAIVER WIRE REPORT" in msg
    assert "CONTESTED CLAIMS" in msg
    assert "Daniel Jones" in msg
    assert "PAID TOO MUCH" in msg
    assert "Panic Pickup" in msg
    assert "STEALS" in msg
    assert "Bargain Bin Bijan" in msg
    assert "Nobody Cares" in msg  # still listed under all executed claims
    assert "**" not in msg  # GroupMe doesn't render markdown - must never leak into messages
