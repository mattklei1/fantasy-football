"""Unit tests for fantasy_football.metrics.lineup_order.
order_flex_pool_by_kickoff - pure logic, no network/DB."""
import datetime

from fantasy_football.metrics.lineup_order import TimedPlayer, order_flex_pool_by_kickoff

EARLY = datetime.datetime(2026, 9, 14, 13, 0)   # 1pm ET early slate
MID = datetime.datetime(2026, 9, 14, 16, 5)     # 4:05pm ET
LATE = datetime.datetime(2026, 9, 14, 20, 20)   # 8:20pm SNF
LATEST = datetime.datetime(2026, 9, 15, 0, 15)  # MNF

SLOT_COUNTS = {"RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 2}


def _player(pid, position, kickoff):
    eligible = {
        "RB": frozenset({"RB", "RB/WR/TE"}),
        "WR": frozenset({"WR", "RB/WR/TE"}),
        "TE": frozenset({"TE", "RB/WR/TE"}),
    }[position]
    return TimedPlayer(player_id=pid, eligible_slots=eligible, kickoff=kickoff)


def test_the_two_latest_kickoffs_land_in_flex():
    players = [
        _player(1, "RB", EARLY), _player(2, "RB", MID),
        _player(3, "WR", EARLY), _player(4, "WR", MID),
        _player(5, "TE", EARLY),
        _player(6, "RB", LATE), _player(7, "WR", LATEST),
    ]
    result = order_flex_pool_by_kickoff(players, SLOT_COUNTS)
    assert len(result) == 7
    flex_players = {pid for pid, slot in result.items() if slot == "RB/WR/TE"}
    assert flex_players == {6, 7}  # the two latest kickoffs


def test_base_slots_hold_earliest_kickoffs_at_their_own_position():
    players = [
        _player(1, "RB", EARLY), _player(2, "RB", MID),
        _player(3, "WR", EARLY), _player(4, "WR", MID),
        _player(5, "TE", EARLY),
        _player(6, "RB", LATE), _player(7, "WR", LATEST),
    ]
    result = order_flex_pool_by_kickoff(players, SLOT_COUNTS)
    assert result[1] == "RB"
    assert result[2] == "RB"
    assert result[3] == "WR"
    assert result[4] == "WR"
    assert result[5] == "TE"


def test_sole_te_forced_into_te_slot_even_with_latest_kickoff():
    # Only one TE-eligible player exists - nothing else can fill the TE
    # slot, so he must go there regardless of kickoff time; the flex
    # slots go to the 2 next-latest among the remaining RB/WR.
    players = [
        _player(1, "RB", EARLY), _player(2, "RB", MID),
        _player(3, "WR", EARLY), _player(4, "WR", LATE),
        _player(5, "TE", LATEST),  # latest kickoff, but sole TE
        _player(6, "RB", MID),
    ]
    slot_counts = {"RB": 2, "WR": 1, "TE": 1, "RB/WR/TE": 2}
    result = order_flex_pool_by_kickoff(players, slot_counts)
    assert result[5] == "TE"
    flex_players = {pid for pid, slot in result.items() if slot == "RB/WR/TE"}
    assert flex_players == {4, 6}  # the 2 latest among the rest


def test_empty_inputs_return_empty():
    assert order_flex_pool_by_kickoff([], SLOT_COUNTS) == {}
    assert order_flex_pool_by_kickoff([_player(1, "RB", EARLY)], {}) == {}
