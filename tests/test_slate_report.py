"""Unit tests for fantasy_football.slate_report - pure MatchupSnapshot/
build_message logic tested directly; the box_scores-consuming functions
(top_individual_scores, worst_lineup_decision, median_cutline) are
tested here against small hand-built stand-ins for espn_api's BoxScore/
Team/Player/League objects (just the attributes these functions actually
read), and also validated against real live espn_api objects separately
(see PROJECT_BRIEF) since a stand-in can't catch a real espn_api shape
mismatch."""
from fantasy_football.slate_report import (
    MatchupSnapshot,
    biggest_blowouts,
    build_message,
    median_cutline,
    worst_lineup_decision,
)


class _MockTeam:
    def __init__(self, team_name):
        self.team_name = team_name


class _MockPlayer:
    def __init__(self, player_id, name, points, slot_position, eligible_slots):
        self.playerId = player_id
        self.name = name
        self.points = points
        self.slot_position = slot_position
        self.eligibleSlots = eligible_slots


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


SLOT_COUNTS = {"RB": 1, "D/ST": 1}


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


def test_build_message_bold_markup_wraps_headers_and_key_stats():
    # **bold** markup is intentional here - GroupMe posting converts it to
    # real Unicode bold via groupme_client.to_groupme_text() before send.
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    msg = build_message("Early slate update", matchups, [("P", "T", 1.0)])
    assert "**EARLY SLATE UPDATE**" in msg
    assert "**1.0**" in msg


# --- biggest_blowouts (now projected, not raw) -----------------------

def test_biggest_blowouts_sorted_by_projected_margin_descending():
    matchups = [
        MatchupSnapshot("Close A", 50.0, "Close B", 48.0, home_projected=95.0, away_projected=25.0),  # big proj gap
        MatchupSnapshot("Rout A", 100.0, "Rout B", 20.0, home_projected=105.0, away_projected=100.0),  # small proj gap
        MatchupSnapshot("Mid A", 60.0, "Mid B", 40.0, home_projected=90.0, away_projected=60.0),
    ]
    result = biggest_blowouts(matchups, limit=2)
    # raw margins would rank Rout(80) > Mid(20) > Close(2); projected margins
    # correctly rank Close(70) > Mid(30) > Rout(5)
    assert [m.home_team for m in result] == ["Close A", "Mid A"]


def test_build_message_includes_biggest_blowout_section_by_projection():
    matchups = [MatchupSnapshot("Rout A", 60.0, "Rout B", 55.0, home_projected=140.0, away_projected=80.0)]
    msg = build_message("Early slate update", matchups, [])
    assert "BIGGEST BLOWOUT (BY PROJECTED FINISH)" in msg
    assert "**Rout A** projected to beat Rout B by **60.0**" in msg


# --- worst_lineup_decision -------------------------------------------------

def test_worst_lineup_decision_flags_team_whose_real_optimal_lineup_would_flip_result():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [
        _MockPlayer(1, "RB Starter", 30.0, "RB", ["RB", "BE"]),
        _MockPlayer(2, "DST Starter", 5.0, "D/ST", ["D/ST", "BE"]),
        _MockPlayer(3, "RB Bench", 60.0, "BE", ["RB", "BE"]),  # legal RB replacement, way better
    ]
    away_lineup = [_MockPlayer(4, "Away Starter", 50.0, "RB", ["RB", "BE"])]
    bs = _MockBoxScore(home, 35.0, home_lineup, away, 50.0, away_lineup)  # 35 = 30+5 actual
    result = worst_lineup_decision([bs], SLOT_COUNTS)
    assert result is not None
    assert result["team"] == "Home Team"
    assert result["optimal_score"] == 65.0  # 60 (better RB) + 5 (DST) > 50


def test_worst_lineup_decision_none_when_bench_upgrade_still_not_enough():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [
        _MockPlayer(1, "RB Starter", 30.0, "RB", ["RB", "BE"]),
        _MockPlayer(2, "DST Starter", 5.0, "D/ST", ["D/ST", "BE"]),
        _MockPlayer(3, "RB Bench", 10.0, "BE", ["RB", "BE"]),  # worse than starter - no real upgrade
    ]
    away_lineup = [_MockPlayer(4, "Away Starter", 50.0, "RB", ["RB", "BE"])]
    bs = _MockBoxScore(home, 35.0, home_lineup, away, 50.0, away_lineup)
    assert worst_lineup_decision([bs], SLOT_COUNTS) is None


def test_worst_lineup_decision_never_flags_a_team_currently_winning():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [
        _MockPlayer(1, "RB Starter", 90.0, "RB", ["RB", "BE"]),
        _MockPlayer(2, "RB Bench", 100.0, "BE", ["RB", "BE"]),
    ]
    away_lineup = [_MockPlayer(3, "Away Starter", 80.0, "RB", ["RB", "BE"])]
    bs = _MockBoxScore(home, 90.0, home_lineup, away, 80.0, away_lineup)
    assert worst_lineup_decision([bs], SLOT_COUNTS) is None  # Home already winning


def test_worst_lineup_decision_ignores_ir_players_even_with_huge_points():
    home = _MockTeam("Home Team")
    away = _MockTeam("Away Team")
    home_lineup = [
        _MockPlayer(1, "RB Starter", 30.0, "RB", ["RB", "BE"]),
        _MockPlayer(2, "DST Starter", 5.0, "D/ST", ["D/ST", "BE"]),
        _MockPlayer(3, "IR Stash", 200.0, "IR", ["RB", "IR"]),  # can't legally be used
    ]
    away_lineup = [_MockPlayer(4, "Away Starter", 50.0, "RB", ["RB", "BE"])]
    bs = _MockBoxScore(home, 35.0, home_lineup, away, 50.0, away_lineup)
    assert worst_lineup_decision([bs], SLOT_COUNTS) is None


def test_worst_lineup_decision_picks_the_single_largest_gap_across_teams():
    # Team A: actual 35, optimal 65 vs opp 50 -> gap 30
    home_a, away_a = _MockTeam("Team A"), _MockTeam("Opp A")
    lineup_a = [
        _MockPlayer(1, "A RB", 30.0, "RB", ["RB", "BE"]),
        _MockPlayer(2, "A DST", 5.0, "D/ST", ["D/ST", "BE"]),
        _MockPlayer(3, "A Bench RB", 60.0, "BE", ["RB", "BE"]),
    ]
    opp_lineup_a = [_MockPlayer(4, "Opp A Starter", 50.0, "RB", ["RB", "BE"])]
    bs_a = _MockBoxScore(home_a, 35.0, lineup_a, away_a, 50.0, opp_lineup_a)

    # Team B: actual 20, optimal 40 vs opp 25 -> gap 20 (smaller than Team A's)
    home_b, away_b = _MockTeam("Team B"), _MockTeam("Opp B")
    lineup_b = [
        _MockPlayer(5, "B RB", 15.0, "RB", ["RB", "BE"]),
        _MockPlayer(6, "B DST", 5.0, "D/ST", ["D/ST", "BE"]),
        _MockPlayer(7, "B Bench RB", 35.0, "BE", ["RB", "BE"]),
    ]
    opp_lineup_b = [_MockPlayer(8, "Opp B Starter", 25.0, "RB", ["RB", "BE"])]
    bs_b = _MockBoxScore(home_b, 20.0, lineup_b, away_b, 25.0, opp_lineup_b)

    result = worst_lineup_decision([bs_a, bs_b], SLOT_COUNTS)
    assert result["team"] == "Team A"


def test_build_message_includes_worst_lineup_decision_section():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    worst = {"team": "Team A", "score": 50.0, "opp_score": 80.0, "optimal_score": 85.0}
    msg = build_message("Early slate update", matchups, [], worst_decision=worst)
    assert "WORST LINEUP DECISION THIS WEEK" in msg
    assert "Team A" in msg
    assert "85.0" in msg


def test_build_message_omits_worst_lineup_decision_section_when_none():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    msg = build_message("Early slate update", matchups, [], worst_decision=None)
    assert "WORST LINEUP DECISION" not in msg


# --- median_cutline (full 12-team list) ------------------------------------

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


def test_median_cutline_none_with_fewer_than_2_teams():
    league = _MockLeague(median_scoring=True)
    # a single real team (e.g. a playoff bye on the other side) - no
    # meaningful cutline can be drawn
    box_scores = [_MockBoxScore(_MockTeam("Team 0"), 0.0, [], None, 0.0, [], home_projected=100.0)]
    assert median_cutline(league, box_scores) is None


def test_median_cutline_returns_all_12_teams_with_correct_diffs():
    league = _MockLeague(median_scoring=True)
    # 12 teams, projected totals 120 down to 10 in steps of 10
    totals = [120, 110, 100, 90, 80, 70, 60, 50, 40, 30, 20, 10]
    box_scores = _make_ranked_box_scores(totals)
    result = median_cutline(league, box_scores)
    assert len(result["teams"]) == 12
    assert result["cut_index"] == 6

    top = result["teams"][0]
    assert top["rank"] == 1 and top["team"] == "Team 0" and top["projected"] == 120
    assert top["diff"] == 50.0  # 120 - cutline(70)
    assert top["making_it"] is True

    cutline_team = result["teams"][5]  # rank 6
    assert cutline_team["team"] == "Team 5" and cutline_team["projected"] == 70
    assert cutline_team["diff"] == 0.0
    assert cutline_team["making_it"] is True

    first_out = result["teams"][6]  # rank 7
    assert first_out["team"] == "Team 6" and first_out["projected"] == 60
    assert first_out["diff"] == -10.0
    assert first_out["making_it"] is False

    last = result["teams"][11]  # rank 12
    assert last["diff"] == -60.0  # 10 - 70


def test_build_message_includes_full_cutline_list_with_marker():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    cutline = {
        "teams": [
            {"rank": 1, "team": "Team In 1", "projected": 100.0, "diff": 30.0, "making_it": True},
            {"rank": 2, "team": "Team Cut", "projected": 70.0, "diff": 0.0, "making_it": True},
            {"rank": 3, "team": "Team Out 1", "projected": 60.0, "diff": -10.0, "making_it": False},
        ],
        "cut_index": 2,
    }
    msg = build_message("Early slate update", matchups, [], cutline=cutline)
    assert "ON THE BUBBLE" in msg
    assert "Team In 1" in msg and "Team Cut" in msg and "Team Out 1" in msg
    cut_marker_idx = msg.index("--- CUTLINE ---")
    assert msg.index("Team Cut") < cut_marker_idx < msg.index("Team Out 1")


def test_build_message_omits_cutline_section_when_none():
    matchups = [MatchupSnapshot("A", 10.0, "B", 5.0)]
    msg = build_message("Early slate update", matchups, [])
    assert "ON THE BUBBLE" not in msg
