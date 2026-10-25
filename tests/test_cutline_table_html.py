"""Unit tests for ui_common.cutline_table_html - pure HTML-string
builder, no DB/network/Streamlit runtime needed."""
from fantasy_football.ui_common import cutline_table_html


def _row(**overrides):
    row = {
        "rank": 1, "team": "Team A", "manager": "Smith", "score": 42.5,
        "projected": 110.3, "vs_cutline": 5.0, "make_pct": 75.0, "p10": 80.0, "p90": 140.0,
        "tone": "win",
    }
    row.update(overrides)
    return row


def test_cutline_table_html_includes_a_score_column():
    # user, 2026-10-25: "In median cutline - add a column for current
    # points scored that week. But still sort by projected" - the
    # column itself just renders whatever `score` the caller put in
    # each row (sort order is the caller's responsibility, see
    # pages/1_Matchups.py, unaffected by this function).
    html = cutline_table_html([_row(score=42.5)])
    assert ">Score<" in html
    assert "42.5" in html


def test_cutline_table_html_score_appears_before_projected_column():
    html = cutline_table_html([_row(score=1.0, projected=2.0)])
    assert html.index(">Score<") < html.index(">Proj<")


def test_cutline_table_html_preserves_row_order_as_given():
    # confirms this function does NOT re-sort - whatever order the
    # caller passes rows in (ranked by projected total) is what renders.
    rows = [_row(rank=1, team="First", score=10.0, projected=120.0), _row(rank=2, team="Second", score=99.0, projected=80.0)]
    html = cutline_table_html(rows)
    assert html.index("First") < html.index("Second")
