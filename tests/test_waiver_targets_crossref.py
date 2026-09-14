"""Unit tests for fantasy_football.waiver_targets_crossref.
find_overlooked_targets - pure logic only, no network/Claude call (that
part, fetch_espn_yahoo_targets, is exercised live like the rest of this
project's Claude-calling functions)."""
import pandas as pd

from fantasy_football.waiver_targets_crossref import find_overlooked_targets


def _board(rows):
    return pd.DataFrame(rows)


def test_matches_a_real_free_agent_by_name_case_and_punctuation_insensitive():
    board = _board(
        [{"player_id": 1, "player_name": "A.J. Brown", "position": "WR", "suggested_bid": 12.0}]
    )
    targets = [{"player_name": "aj brown", "position": "WR", "source": "ESPN"}]
    overlooked = find_overlooked_targets(targets, board, already_suggested_player_ids=set())
    assert len(overlooked) == 1
    assert overlooked[0]["player_id"] == 1
    assert overlooked[0]["source"] == "ESPN"


def test_skips_a_target_not_found_in_our_own_free_agent_board():
    board = _board([{"player_id": 1, "player_name": "A.J. Brown", "position": "WR", "suggested_bid": 12.0}])
    targets = [{"player_name": "Nobody Rostered Here", "position": "WR", "source": "Yahoo"}]
    assert find_overlooked_targets(targets, board, already_suggested_player_ids=set()) == []


def test_skips_a_target_already_in_our_own_suggestions():
    board = _board([{"player_id": 1, "player_name": "A.J. Brown", "position": "WR", "suggested_bid": 12.0}])
    targets = [{"player_name": "A.J. Brown", "position": "WR", "source": "ESPN"}]
    assert find_overlooked_targets(targets, board, already_suggested_player_ids={1}) == []


def test_deduplicates_the_same_player_named_by_both_sites():
    board = _board([{"player_id": 1, "player_name": "A.J. Brown", "position": "WR", "suggested_bid": 12.0}])
    targets = [
        {"player_name": "A.J. Brown", "position": "WR", "source": "ESPN"},
        {"player_name": "AJ Brown", "position": "WR", "source": "Yahoo"},
    ]
    overlooked = find_overlooked_targets(targets, board, already_suggested_player_ids=set())
    assert len(overlooked) == 1


def test_empty_board_or_empty_targets_returns_empty():
    assert find_overlooked_targets([{"player_name": "X"}], pd.DataFrame(), set()) == []
    assert find_overlooked_targets([], _board([{"player_id": 1, "player_name": "X", "position": "WR", "suggested_bid": 1.0}]), set()) == []
