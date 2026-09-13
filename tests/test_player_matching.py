"""Unit tests for fantasy_football.player_matching - pure functions, no
network/DB (per PROJECT_BRIEF testing requirements)."""
import pytest

from fantasy_football.player_matching import (
    match_players_for_position,
    normalize_name,
    parse_pos_rank,
)


def test_normalize_name_strips_apostrophes_hyphens_and_suffixes():
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("DK Metcalf") == "dk metcalf"


def test_normalize_name_same_result_both_directions():
    # the exact scenario that motivated normalization: two sources
    # spelling the same real player slightly differently must collide
    assert normalize_name("Kenneth Walker III") == normalize_name("Kenneth Walker")


def test_parse_pos_rank_extracts_trailing_int():
    assert parse_pos_rank("QB1") == 1
    assert parse_pos_rank("WR23") == 23
    assert parse_pos_rank(None) is None
    assert parse_pos_rank("") is None


def test_match_by_name_matches_unique_names():
    espn_players = [
        {"player_id": 101, "player_name": "Josh Allen"},
        {"player_id": 102, "player_name": "Kenneth Walker III"},
    ]
    fp_players = [
        {"player_id": 1, "player_name": "Josh Allen"},
        {"player_id": 2, "player_name": "Kenneth Walker III"},
        {"player_id": 3, "player_name": "Some Undrafted Guy"},
    ]
    matches = match_players_for_position(espn_players, fp_players, "QB")
    assert matches == {1: 101, 2: 102}
    # not rostered anywhere -> correctly unmatched, not a crash
    assert 3 not in matches


def test_match_by_name_skips_ambiguous_duplicates():
    # two different real players who happen to share a normalized name
    # and are both currently rostered - must not guess, skip both
    espn_players = [
        {"player_id": 101, "player_name": "Michael Thomas"},
        {"player_id": 102, "player_name": "Michael Thomas"},
    ]
    fp_players = [{"player_id": 1, "player_name": "Michael Thomas"}]
    matches = match_players_for_position(espn_players, fp_players, "WR")
    assert matches == {}


def test_match_prefers_fantasypros_espn_id_map_over_name_matching():
    # deliberately mismatched names (as if FantasyPros hadn't updated a
    # display name yet) - the espn_id cross-reference should still win
    espn_players = [{"player_id": 101, "player_name": "Josh Allen"}]
    fp_players = [{"player_id": 55, "player_name": "Joshua Allen"}]
    espn_id_map = {55: 101}
    matches = match_players_for_position(espn_players, fp_players, "QB", espn_id_map=espn_id_map)
    assert matches == {55: 101}


def test_match_falls_back_to_name_when_not_in_espn_id_map():
    espn_players = [{"player_id": 101, "player_name": "Josh Allen"}]
    fp_players = [{"player_id": 55, "player_name": "Josh Allen"}]
    # espn_id_map has entries for OTHER players, but not this one
    espn_id_map = {999: 888}
    matches = match_players_for_position(espn_players, fp_players, "QB", espn_id_map=espn_id_map)
    assert matches == {55: 101}


def test_match_ignores_espn_id_map_entry_pointing_outside_this_position():
    # a stale/irrelevant cross-reference entry pointing at a player who
    # isn't in this position's rostered pool at all must not be trusted
    espn_players = [{"player_id": 101, "player_name": "Josh Allen"}]
    fp_players = [{"player_id": 55, "player_name": "Josh Allen"}]
    espn_id_map = {55: 999999}  # not in espn_players
    matches = match_players_for_position(espn_players, fp_players, "QB", espn_id_map=espn_id_map)
    # falls through to name matching and still finds the right player
    assert matches == {55: 101}


def test_match_dst_by_team_abbreviation():
    espn_players = [
        {"player_id": 101, "player_name": "Texans D/ST", "pro_team": "HOU"},
        {"player_id": 102, "player_name": "Commanders D/ST", "pro_team": "WSH"},
    ]
    fp_players = [
        {"player_id": 1, "player_name": "Houston Texans", "player_team_id": "HOU"},
        # FantasyPros spells this "WAS" - the one team abbreviation ESPN
        # spells differently ("WSH") - the mapping must bridge this
        {"player_id": 2, "player_name": "Washington Commanders", "player_team_id": "WAS"},
    ]
    matches = match_players_for_position(espn_players, fp_players, "D/ST")
    assert matches == {1: 101, 2: 102}
