"""Unit tests for fantasy_football.lineup_alert_report - pure string
formatting, no network/DB."""
from fantasy_football.lineup_alert_report import build_sunday_message, build_wednesday_message


def _result(changes=None, zero_proj=None, week=2):
    return {"changes": changes or [], "zero_projected_starters": zero_proj or [], "week": week}


def _change(name="Bench Guy", position="RB", from_slot="BE", to_slot="RB/WR/TE"):
    return {"player_id": 1, "player_name": name, "position": position, "from_slot": from_slot, "to_slot": to_slot}


def _zero(name="Hurt Guy", projected=0.0):
    return {"player_id": 2, "player_name": name, "projected": projected}


# --- Wednesday --------------------------------------------------------

def test_wednesday_no_changes_says_so():
    msg = build_wednesday_message(_result(), "McConkey Kong")
    assert "MCCONKEY KONG" in msg
    assert "already matches the weekly consensus" in msg
    assert "Suggested changes" not in msg


def test_wednesday_lists_changes_and_points_to_war_room():
    msg = build_wednesday_message(_result(changes=[_change()]), "Team")
    assert "Suggested changes" in msg
    assert "Bench Guy (RB): BE -> RB/WR/TE" in msg
    assert "War Room -> Lineup Optimizer" in msg


def test_wednesday_no_urgency_language():
    msg = build_wednesday_message(_result(changes=[_change()]), "Team")
    assert "HIGH PRIORITY" not in msg
    assert "ALERT" not in msg


# --- Sunday -------------------------------------------------------------

def test_sunday_high_priority_comes_before_non_optimal_section():
    msg = build_sunday_message(_result(changes=[_change()], zero_proj=[_zero()]), "Team")
    priority_idx = msg.index("HIGH PRIORITY")
    nonoptimal_idx = msg.index("NON-OPTIMAL")
    assert priority_idx < nonoptimal_idx


def test_sunday_zero_projected_section_lists_players_with_points():
    msg = build_sunday_message(_result(zero_proj=[_zero("Hurt Guy", 0.0)]), "Team")
    assert "Hurt Guy (0.0 pts projected)" in msg


def test_sunday_says_none_zero_projected_when_empty():
    msg = build_sunday_message(_result(changes=[_change()]), "Team")
    assert "No starters projected for 0 points." in msg


def test_sunday_says_matches_consensus_when_no_changes():
    msg = build_sunday_message(_result(zero_proj=[_zero()]), "Team")
    assert "already matches the weekly consensus" in msg


def test_sunday_omits_war_room_pointer_when_everything_is_fine():
    msg = build_sunday_message(_result(), "Team")
    assert "War Room" not in msg


def test_sunday_includes_war_room_pointer_when_anything_needs_attention():
    msg = build_sunday_message(_result(zero_proj=[_zero()]), "Team")
    assert "War Room -> Lineup Optimizer" in msg
