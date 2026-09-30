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


def test_fetch_week_claims_prefers_a_failed_status_over_a_stale_pending_retry():
    # ESPN can log the SAME team's claim for the SAME player twice across
    # its own processing retries (a PENDING placeholder alongside the
    # real terminal FAILED_* outcome) - the more informative status
    # should win, not just whichever transaction happened to come first.
    txns = [
        _txn(_team("Team A"), "EXECUTED", 5, [_item(1, "Player X")]),
        _txn(_team("Team B"), "PENDING", 8, [_item(1, "Player X")]),
        _txn(_team("Team B"), "FAILED_PLAYERALREADYDROPPED", 8, [_item(1, "Player X")]),
    ]
    claims = fetch_week_claims(_FakeLeague(txns), scoring_period=1)
    c = claims[0]
    team_b_entry = next(entry for entry in c.all_bids if entry[0] == "Team B")
    assert team_b_entry == ("Team B", 8, "FAILED_PLAYERALREADYDROPPED")


def test_fetch_week_claims_picks_the_highest_bid_among_equally_ranked_retries():
    # a team can place several real backup claims for the same player at
    # DIFFERENT price points with different drop targets (seen live
    # 2026-09-30: McConkey Kong tried Jaylen Wright at $1, $2, and $4
    # across 3 separate claims, two of them tied at FAILED_
    # INVALIDPLAYERSOURCE) - the most aggressive real amount should win
    # the tie, not whichever one happened to be listed first.
    txns = [
        _txn(_team("Team A"), "EXECUTED", 6, [_item(1, "Player X")]),
        _txn(_team("Team B"), "PENDING", 1, [_item(1, "Player X")]),
        _txn(_team("Team B"), "FAILED_INVALIDPLAYERSOURCE", 4, [_item(1, "Player X")]),
        _txn(_team("Team B"), "PENDING", 1, [_item(1, "Player X")]),
        _txn(_team("Team B"), "FAILED_INVALIDPLAYERSOURCE", 2, [_item(1, "Player X")]),
    ]
    claims = fetch_week_claims(_FakeLeague(txns), scoring_period=1)
    team_b_entry = next(entry for entry in claims[0].all_bids if entry[0] == "Team B")
    assert team_b_entry == ("Team B", 4, "FAILED_INVALIDPLAYERSOURCE")


def test_build_message_flags_a_higher_losing_bid_that_failed_for_a_non_amount_reason():
    # real bug found 2026-09-30 (user: "Kendre miller has a losing bid at
    # a higher $ amount") - Doody Guac Boys' $8 bid lost to a $5 winner
    # not because it was outbid, but because their claim's own intended
    # drop had already been used by an earlier claim of theirs that same
    # run (FAILED_PLAYERALREADYDROPPED). The report must call this out,
    # not just silently list the raw dollar amounts.
    claim = PlayerClaimResult(
        1, "Kendre Miller", "RB", "Jerusalem Price Fixers", 5.0,
        all_bids=[
            ("Jerusalem Price Fixers", 5.0, "EXECUTED"),
            ("Doody Guac Boys", 8.0, "FAILED_PLAYERALREADYDROPPED"),
            ("McConkey Kong", 1.0, "FAILED_INVALIDPLAYERSOURCE"),
        ],
    )
    msg = build_message([claim], week=4)
    assert "Doody Guac Boys's $8 bid was actually higher" in msg
    assert "already gone by the time it processed" in msg
    # a normal lower loss (McConkey Kong, plain outbid) gets no footnote
    assert "McConkey Kong's $1 bid" not in msg


def test_build_message_no_footnote_for_a_plain_outbid_loss_even_if_amounts_look_close():
    # FAILED_INVALIDPLAYERSOURCE is the normal "someone else's claim got
    # there first" status - a losing bid with this status should never
    # get the higher-bid footnote, even though it lost.
    claim = PlayerClaimResult(
        1, "Ollie Gordon II", "RB", "Doody Guac Boys", 62.0,
        all_bids=[
            ("Doody Guac Boys", 62.0, "EXECUTED"),
            ("Lamar Comeback SZN", 51.0, "FAILED_INVALIDPLAYERSOURCE"),
        ],
    )
    msg = build_message([claim], week=4)
    assert "↳" not in msg


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
        all_bids=[("Doody Guac Boys", 12.0, "EXECUTED"), ("Team B", 5.0, "FAILED_INVALIDPLAYERSOURCE"),
                  ("Team C", 2.0, "FAILED_INVALIDPLAYERSOURCE")],
        suggested_bid=8.0,
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
    # "ALL EXECUTED CLAIMS" summary removed (user, 2026-09-16: "remove the
    # summary of all claims") - a quiet claim not flagged in any section
    # above no longer appears anywhere in the message at all.
    assert "ALL EXECUTED CLAIMS" not in msg
    assert "Nobody Cares" not in msg
    # **bold** markup is intentional - groupme_client.send_long_message
    # converts it to real Unicode bold before posting.
    assert "**Daniel Jones**" in msg


def test_build_message_contested_claim_does_not_repeat_the_winning_bid():
    # user, 2026-09-16: "when recapping the contested bids, don't repeat
    # the winning bid. You already said it before the parentheses" - the
    # winner's own bid ($12) is stated once up front; the parenthetical
    # list should only show the OTHER (losing) bidders, not restate it.
    contested = PlayerClaimResult(
        1, "Daniel Jones", "QB", "Doody Guac Boys", 12.0,
        all_bids=[("Doody Guac Boys", 12.0, "EXECUTED"), ("Team B", 5.0, "FAILED_INVALIDPLAYERSOURCE"),
                  ("Team C", 2.0, "FAILED_INVALIDPLAYERSOURCE")],
        suggested_bid=8.0,
    )
    msg = build_message([contested], week=5)
    contested_line = next(line for line in msg.splitlines() if "Daniel Jones" in line)
    assert contested_line.count("$12") == 1
    assert "Team B $5" in contested_line
    assert "Team C $2" in contested_line
    assert "Doody Guac Boys $12" not in contested_line


def test_build_message_contested_claim_omits_bidder_count():
    # user, 2026-09-16: "remove '2 bids:' It's clear by listing what the
    # other bids were how many bids there were" - no bidder-count prefix
    # at all, just the winner and the other bids.
    contested = PlayerClaimResult(
        1, "Daniel Jones", "QB", "Doody Guac Boys", 12.0,
        all_bids=[("Doody Guac Boys", 12.0, "EXECUTED"), ("Team B", 5.0, "FAILED_INVALIDPLAYERSOURCE")],
        suggested_bid=8.0,
    )
    msg = build_message([contested], week=5)
    contested_line = next(line for line in msg.splitlines() if "Daniel Jones" in line)
    assert "bidder" not in contested_line.lower()
    assert "also bid: Team B $5" in contested_line


def test_build_message_no_double_blank_line_before_footer():
    steal = PlayerClaimResult(1, "Bargain Bin Bijan", "RB", "Roses to Flowers", 2.0, suggested_bid=25.0)
    msg = build_message([steal], week=5)
    assert "\n\n\n" not in msg


def test_build_message_quiet_but_not_empty_week_still_says_something():
    # a claim processed with nothing contested/overpaid/underpaid used to
    # only show up in the now-removed "ALL EXECUTED CLAIMS" list - must
    # still surface SOME real content, not a bare header.
    quiet = PlayerClaimResult(1, "Nobody Cares", "K", "Roses to Flowers", 1.0, suggested_bid=1.0)
    msg = build_message([quiet], week=5)
    assert "WEEK 5 WAIVER WIRE REPORT" in msg
    assert "1 claim" in msg
