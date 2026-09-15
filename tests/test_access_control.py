"""Unit tests for fantasy_football.access_control - real filesystem I/O
against a temp path (monkeypatched ACCESS_PATH), no network/DB. Commit
behavior degrades to "local only" since no GITHUB_TOKEN is configured
in the test env by default - same convention as test_league_registry.py."""
from fantasy_football import access_control


def _use_tmp_path(monkeypatch, tmp_path):
    monkeypatch.setattr(access_control, "ACCESS_PATH", tmp_path / "league_access.json")
    monkeypatch.setattr("fantasy_football.config.github_token", lambda: None)
    monkeypatch.setattr("fantasy_football.config.admin_email", lambda: "admin@example.com")


def test_load_access_empty_when_no_file(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert access_control.load_access() == {}


def test_set_user_access_then_load_round_trips(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    access_control.set_user_access("Manager@Example.com", [111, 222], True)
    access = access_control.load_access()
    assert access == {"manager@example.com": {"leagues": [111, 222], "war_room": True}}


def test_set_user_access_with_nothing_granted_removes_entry(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    access_control.set_user_access("manager@example.com", [111], True)
    access_control.set_user_access("manager@example.com", [], False)
    assert access_control.load_access() == {}


def test_remove_user_access(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    access_control.set_user_access("manager@example.com", [111], False)
    access_control.remove_user_access("manager@example.com")
    assert access_control.load_access() == {}


def test_get_user_leagues_primary_admin_sees_all(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert access_control.get_user_leagues("admin@example.com", 1025842) == "all"


def test_get_user_leagues_ungranted_user_sees_only_primary(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert access_control.get_user_leagues("nobody@example.com", 1025842) == [1025842]


def test_get_user_leagues_granted_user_sees_primary_plus_extras(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    access_control.set_user_access("manager@example.com", [111, 222], False)
    assert access_control.get_user_leagues("manager@example.com", 1025842) == [1025842, 111, 222]


def test_get_user_leagues_dedupes_primary_if_also_explicitly_granted(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    access_control.set_user_access("manager@example.com", [1025842, 222], False)
    assert access_control.get_user_leagues("manager@example.com", 1025842) == [1025842, 222]


def test_has_war_room_access_primary_admin_always_true(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert access_control.has_war_room_access("admin@example.com") is True


def test_has_war_room_access_false_by_default(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    assert access_control.has_war_room_access("manager@example.com") is False


def test_has_war_room_access_true_when_granted(monkeypatch, tmp_path):
    _use_tmp_path(monkeypatch, tmp_path)
    access_control.set_user_access("manager@example.com", [], True)
    assert access_control.has_war_room_access("manager@example.com") is True


def test_load_access_ignores_malformed_json(monkeypatch, tmp_path):
    path = tmp_path / "league_access.json"
    path.write_text("{not valid")
    monkeypatch.setattr(access_control, "ACCESS_PATH", path)
    assert access_control.load_access() == {}
