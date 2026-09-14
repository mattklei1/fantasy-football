"""Unit tests for fantasy_football.protected_players - real filesystem
I/O against a temp directory (monkeypatched PROTECTED_DIR), no network/DB."""
from fantasy_football import protected_players


def _use_tmp_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(protected_players, "PROTECTED_DIR", tmp_path / "protected_players")


def test_load_protected_returns_empty_set_when_nothing_saved(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    assert protected_players.load_protected(1) == set()


def test_save_and_load_round_trips(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    protected_players.save_protected(1, {100, 200, 300})
    assert protected_players.load_protected(1) == {100, 200, 300}


def test_different_teams_are_independent(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    protected_players.save_protected(1, {100})
    protected_players.save_protected(2, {200})
    assert protected_players.load_protected(1) == {100}
    assert protected_players.load_protected(2) == {200}


def test_save_overwrites_previous_list_rather_than_merging(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    protected_players.save_protected(1, {100, 200})
    protected_players.save_protected(1, {300})
    assert protected_players.load_protected(1) == {300}


def test_save_empty_set_clears_the_list(monkeypatch, tmp_path):
    _use_tmp_dir(monkeypatch, tmp_path)
    protected_players.save_protected(1, {100})
    protected_players.save_protected(1, set())
    assert protected_players.load_protected(1) == set()
