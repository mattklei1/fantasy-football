"""Unit tests for fantasy_football.rank_overrides - real filesystem I/O
against a temp directory (monkeypatched OVERRIDES_DIR), no network/DB."""
from fantasy_football import rank_overrides


def _use_tmp_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(rank_overrides, "OVERRIDES_DIR", tmp_path / "rank_overrides")


def test_save_and_load_round_trips_normalized_name_to_rank(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    ranks = [{"rank": 1, "player_name": "Ja'Marr Chase"}, {"rank": 2, "player_name": "Justin Jefferson"}]
    rank_overrides.save_override(2026, 3, ranks, "yahoo_week3.pdf")

    loaded = rank_overrides.load_override(2026, 3)
    assert loaded == {"jamarr chase": 1, "justin jefferson": 2}


def test_load_override_returns_none_when_nothing_uploaded(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    assert rank_overrides.load_override(2026, 5) is None


def test_load_override_meta_includes_source_and_timestamp(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    rank_overrides.save_override(2026, 3, [{"rank": 1, "player_name": "Bijan Robinson"}], "cheat_sheet.pdf")

    meta = rank_overrides.load_override_meta(2026, 3)
    assert meta["season"] == 2026
    assert meta["week"] == 3
    assert meta["source_filename"] == "cheat_sheet.pdf"
    assert meta["uploaded_at"]
    assert meta["ranks"] == [{"rank": 1, "player_name": "Bijan Robinson"}]


def test_clear_override_removes_file_and_reports_whether_one_existed(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    assert rank_overrides.clear_override(2026, 3) is False  # nothing to clear yet

    rank_overrides.save_override(2026, 3, [{"rank": 1, "player_name": "Bijan Robinson"}], "x.pdf")
    assert rank_overrides.clear_override(2026, 3) is True
    assert rank_overrides.load_override(2026, 3) is None


def test_different_weeks_are_independent(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    rank_overrides.save_override(2026, 3, [{"rank": 1, "player_name": "Week Three Guy"}], "a.pdf")
    rank_overrides.save_override(2026, 4, [{"rank": 1, "player_name": "Week Four Guy"}], "b.pdf")

    assert rank_overrides.load_override(2026, 3) == {"week three guy": 1}
    assert rank_overrides.load_override(2026, 4) == {"week four guy": 1}
