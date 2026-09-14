"""Unit tests for fantasy_football.rank_override_pdf.parse_ranking_lines -
pure text parsing, no real PDF bytes/pdfplumber needed (see module
docstring for why the regex logic is split out from PDF extraction)."""
from fantasy_football.rank_override_pdf import parse_ranking_lines


def test_parses_simple_numbered_list():
    text = "1. Ja'Marr Chase\n2. Justin Jefferson\n3) CeeDee Lamb"
    rows = parse_ranking_lines(text)
    assert [(r.rank, r.player_name) for r in rows] == [
        (1, "Ja'Marr Chase"), (2, "Justin Jefferson"), (3, "CeeDee Lamb"),
    ]


def test_trims_trailing_team_and_position_codes():
    text = "1  Christian McCaffrey  SF RB\n2  Amon-Ra St. Brown  DET WR Bye: 5"
    rows = parse_ranking_lines(text)
    assert rows[0].player_name == "Christian McCaffrey"
    assert rows[1].player_name == "Amon-Ra St. Brown"


def test_skips_header_and_footer_lines():
    text = "Rank  Player  Team  Pos\n1. Puka Nacua\nPage 3 of 10\n3/15/2026"
    rows = parse_ranking_lines(text)
    assert [r.player_name for r in rows] == ["Puka Nacua"]


def test_skips_blank_lines():
    text = "1. Bijan Robinson\n\n\n2. Breece Hall"
    rows = parse_ranking_lines(text)
    assert len(rows) == 2


def test_preserves_raw_line_for_review_ui():
    text = "7. Davante Adams LV WR"
    rows = parse_ranking_lines(text)
    assert rows[0].raw_line == "7. Davante Adams LV WR"


def test_empty_text_returns_empty_list():
    assert parse_ranking_lines("") == []


def test_skips_short_junk_match():
    # a leading number followed by a too-short "name" (e.g. a lone initial)
    text = "4 A"
    rows = parse_ranking_lines(text)
    assert rows == []
