"""Unit tests for fantasy_football.github_sync - no real network calls
(requests.get/put/delete are monkeypatched)."""
from fantasy_football import github_sync


class _FakeResponse:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


def test_commit_file_creates_new_file_when_none_exists(monkeypatch):
    calls = {}

    def fake_get(url, headers, params, timeout):
        return _FakeResponse(404)

    def fake_put(url, headers, json, timeout):
        calls["put"] = (url, json)
        return _FakeResponse(201, {"commit": {"sha": "abc123"}})

    monkeypatch.setattr(github_sync.requests, "get", fake_get)
    monkeypatch.setattr(github_sync.requests, "put", fake_put)

    result = github_sync.commit_file("me/repo", "tok", "path/file.json", b"hello", "msg")

    assert result == {"success": True, "status_code": 201, "message": "Committed", "commit_sha": "abc123"}
    assert "sha" not in calls["put"][1]  # new file - no sha sent


def test_commit_file_includes_existing_sha_when_updating(monkeypatch):
    calls = {}

    def fake_put(url, headers, json, timeout):
        calls["put"] = json
        return _FakeResponse(200, {"commit": {"sha": "new-sha"}})

    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(200, {"sha": "old-sha"}))
    monkeypatch.setattr(github_sync.requests, "put", fake_put)

    result = github_sync.commit_file("me/repo", "tok", "path/file.json", b"hello", "msg")

    assert calls["put"]["sha"] == "old-sha"
    assert result["success"] is True
    assert result["commit_sha"] == "new-sha"


def test_commit_file_reports_failure_on_non_2xx(monkeypatch):
    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(404))
    monkeypatch.setattr(github_sync.requests, "put", lambda *a, **k: _FakeResponse(403, {"message": "Bad credentials"}))

    result = github_sync.commit_file("me/repo", "bad-tok", "path/file.json", b"hello", "msg")

    assert result["success"] is False
    assert result["status_code"] == 403
    assert result["message"] == "Bad credentials"


def test_delete_file_is_a_noop_success_when_file_does_not_exist(monkeypatch):
    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(404))
    result = github_sync.delete_file("me/repo", "tok", "path/file.json", "msg")
    assert result == {"success": True, "status_code": None, "message": "Nothing to delete", "commit_sha": None}


def test_delete_file_sends_sha_and_reports_success(monkeypatch):
    calls = {}

    def fake_delete(url, headers, json, timeout):
        calls["delete"] = json
        return _FakeResponse(200, {"commit": {"sha": "sha2"}})

    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(200, {"sha": "sha1"}))
    monkeypatch.setattr(github_sync.requests, "delete", fake_delete)

    result = github_sync.delete_file("me/repo", "tok", "path/file.json", "msg")

    assert calls["delete"]["sha"] == "sha1"
    assert result["success"] is True
    assert result["commit_sha"] == "sha2"
