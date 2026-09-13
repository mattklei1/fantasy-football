"""Unit tests for fantasy_football.slate_report - pure MatchupSnapshot/
build_message logic tested directly; the box_scores-consuming functions
(top_individual_scores, bench_would_be_winning, median_cutline) are
tested here against small hand-built stand-ins for espn_api's BoxScore/
Team/Player/League objects (just the attributes these functions actually
read), and also validated against real live espn_api objects separately
(see PROJECT_BRIEF) since a stand-in can't catch a real espn_api shape
mismatch."""
from fantasy_football.slate_report import (
    MatchupSnapshot,
    bench_would_be_winning,
    biggest_blowouts,
    build_message,
    median_cutline,
)


class _MockTeam:
    def __init__(self, team_name):
        self.team_name = team_name


class _MockPlayer:
    def __init__(self, name, points, slot_position):
        self.name = name
        self.points = points
        self.slot_position = slot_position


class _MockBoxScore:
    def __init__(
        self, home_team, home_score, home_lineup, away_team, away_score, away_lineup,
        home_projected=0.0, away_projected=0.0,
    ):
        self.home_team = home_team
        self.home_score = home_score
        self.home_lineup = home_lineup
        self.away_team = away_team
        self.away_score = away_score
        self.away_lineup = away_lineup
        self.home_projected = home_projected
        self.away_projected = away_projected


class _MockSettings:
    def __init__(self, median_scoring):
        self.median_scoring = median_scoring


class _MockLeague:
    def __init__(self, median_scoring):
        self.settings = _MockSettings(median_scoring)


def test_margin_is_absolute():
    m = MatchupSnapshot("A", 50.0, "B", 80.0)
    assert m.margin == 30.0
    m2 = MatchupSnapshot("A", 80.0, "B", 50.0)
    assert m2.margin == 30.0


def test_build_message_empty_matchups():
    msg = build_message("Early slate update", [], [])
    assert "no games in progress" in msg.lower()


def test_build_message_sorts_by_projected_margin_not_raw_score_margin():
    # The real bug being fixed: a huge score-so-far gap where the trailing
    # team simply hasn't played yet should NOT be treated as "not close" -
    # projected finish is what actually matters.
    matchups = [
        MatchupSnapshot("Real Rout A", 100.0, "Real Rout B", 20.0, home_projected=105.0, away_projected=25.0),
        MatchupSnapshot("Not Actually Close A", 60.0, "Not Actually Close B", 5.0, home_projected=95.0, away_projected=90.0),
        MatchupSnapshot("Middling A", 60.0, "Middling B", 45.0, home_projected=90.0, away_projected=70.0),
    ]
    msg = build_message("Early slate update", matchups, [])
    not_actually_close_idx = msg.index("Not Actually Close A")
    middling_idx = msg.index("Middling A")
    real_rout_idx = msg.index("Real Rout A")
    # raw score margins would rank Middling(15) < Real Rout(80) < Not Actually Close(55);
    # projected margins correctly rank Not Actually Close(5) < Middling(20) < Real Rout(80)
    assert not_actually_close_idx < middling_idx < real_rout_idx


def test_matchup_snapshot_str_includes_projection_when_present():
    m = MatchupSnapshot("A", 10.0, "B", 5.0, home_projected=95.0, away_projected=90.0)
    text = str(m)
    assert "95.0" in text and "90.0" in text


def test_matchup_snapshot_str_omits_projection_when_absent():
    m = MatchupSnapshot("A", 10.0, "B", 5.0)
    assert "proj" not in str(m)


def test_projected_margin_is_absolute():
    m = MatchupSnapshot("A", 10.0, "B", 5.0, home_projected=80.0, away_projected=100.0)
    assert m.projected_margin == 20.0


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


# --- biggest_blowouts -------------------------------------------------

def test_biggest_blowouts_sorted_by_raw_margin_descending():
    matchups = [
        MatchupSnapshot("Close A", 50.0, "Close B", 48.0),
        MatchupSnapshot("Rout A", 100.0, "Rout B", 20.0),
        MatchupSnapshot("Mid A", 60.0, "Mid B", 40.0),
    ]
    result = biggest_blowouts(matchups, limit=2)
    assert [m.home_team for m in result] == ["Rout A", "Mid A"]


def test_build_message_includes_biggest_blowout_section():
    matchups = [MatchupSnapshot("Rout A", 100.0, "Rout B", 20.0)]
    msg = build_message("Early slate update", matchups, [])
    assert "BIGGEST BLOWOUT RIGHT NOW" in msg
    assert "Rout A is running away with it" in msg


# --- bench_would_be_winning ---------------------------------------------

def test_bench_flags_losing_team_whose_bench_would_flip_the_result():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [_MockPlayer("Home Starter", 50.0, "QB"), _MockPlayer("Home Bench", 40.0, "BE")]
    away_lineup = [_MockPlayer("Away Starter", 80.0, "QB")]
    bs = _MockBoxScore(home, 50.0, home_lineup, away, 80.0, away_lineup)
    result = bench_would_be_winning([bs])
    assert len(result) == 1
    assert result[0]["team"] == "Home Team"
    assert result[0]["bench_points"] == 40.0


def test_bench_does_not_flag_team_whose_bench_would_not_flip_result():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [_MockPlayer("Home Starter", 50.0, "QB"), _MockPlayer("Home Bench", 5.0, "BE")]
    away_lineup = [_MockPlayer("Away Starter", 80.0, "QB")]
    bs = _MockBoxScore(home, 50.0, home_lineup, away, 80.0, away_lineup)
    assert bench_would_be_winning([bs]) == []


def test_bench_never_flags_a_team_currently_winning():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [_MockPlayer("Home Starter", 90.0, "QB"), _MockPlayer("Home Bench", 100.0, "BE")]
    away_lineup = [_MockPlayer("Away Starter", 80.0, "QB")]
    bs = _MockBoxScore(home, 90.0, home_lineup, away, 80.0, away_lineup)
    assert bench_would_be_winning([bs]) == []  # Home is already winning - not eligible


def test_build_message_includes_bench_would_be_winning_section():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    flips = [{"team": "Team A", "score": 50.0, "opp_score": 80.0, "bench_points": 40.0}]
    msg = build_message("Early slate update", matchups, [], bench_flips=flips)
    assert "BENCH WOULD BE WINNING" in msg
    assert "Team A" in msg


# --- median_cutline -------------------------------------------------------

def _make_ranked_box_scores(projected_totals: list[float]) -> list[_MockBoxScore]:
    """One box score per pair of consecutive projected totals."""
    box_scores = []
    for i in range(0, len(projected_totals), 2):
        home = _MockTeam(f"Team {i}")
        away = _MockTeam(f"Team {i + 1}")
        box_scores.append(
            _MockBoxScore(
                home, 0.0, [], away, 0.0, [],
                home_projected=projected_totals[i], away_projected=projected_totals[i + 1],
            )
        )
    return box_scores


def test_median_cutline_none_when_season_is_not_median_scoring():
    league = _MockLeague(median_scoring=False)
    box_scores = _make_ranked_box_scores([100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 5, 1])
    assert median_cutline(league, box_scores) is None


def test_median_cutline_none_with_fewer_than_8_teams():
    league = _MockLeague(median_scoring=True)
    box_scores = _make_ranked_box_scores([100, 90, 80, 70, 60, 50])  # only 6 teams
    assert median_cutline(league, box_scores) is None


def test_median_cutline_picks_ranks_6_7_8_by_projected_total():
    league = _MockLeague(median_scoring=True)
    # 12 teams, projected totals 120 down to 10 in steps of 10
    totals = [120, 110, 100, 90, 80, 70, 60, 50, 40, 30, 20, 10]
    box_scores = _make_ranked_box_scores(totals)
    result = median_cutline(league, box_scores)
    assert result["making_it"] == ("Team 5", 70)   # 6th highest
    assert result["missing_it"] == ("Team 6", 60)  # 7th highest
    assert result["also_missing_it"] == ("Team 7", 50)  # 8th highest


def test_build_message_includes_cutline_section_when_present():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    cutline = {"making_it": ("Team In", 90.0), "missing_it": ("Team Out", 80.0), "also_missing_it": ("Team Out2", 70.0)}
    msg = build_message("Early slate update", matchups, [], cutline=cutline)
    assert "ON THE BUBBLE" in msg
    assert "Team In" in msg and "Team Out" in msg and "Team Out2" in msg


def test_build_message_omits_cutline_section_when_none():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    msg = build_message("Early slate update", matchups, [])
    assert "ON THE BUBBLE" not in msg
