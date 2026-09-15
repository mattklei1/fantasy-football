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


# --- branch_exists / ensure_branch_exists -------------------------------

def test_branch_exists_true_on_200(monkeypatch):
    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(200))
    assert github_sync.branch_exists("me/repo", "tok", "data-snapshots") is True


def test_branch_exists_false_on_404(monkeypatch):
    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(404))
    assert github_sync.branch_exists("me/repo", "tok", "data-snapshots") is False


def test_ensure_branch_exists_is_a_noop_when_already_there(monkeypatch):
    calls = {"post": 0}
    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(200))
    monkeypatch.setattr(github_sync.requests, "post", lambda *a, **k: calls.__setitem__("post", calls["post"] + 1))

    result = github_sync.ensure_branch_exists("me/repo", "tok", "data-snapshots", "main")

    assert result["success"] is True
    assert calls["post"] == 0  # never tried to create it


def test_ensure_branch_exists_creates_from_base_branch_head(monkeypatch):
    calls = {}

    def fake_get(url, headers, timeout, **kwargs):
        if url.endswith("/git/ref/heads/data-snapshots"):
            return _FakeResponse(404)
        if url.endswith("/git/ref/heads/main"):
            return _FakeResponse(200, {"object": {"sha": "main-head-sha"}})
        raise AssertionError(f"unexpected GET {url}")

    def fake_post(url, headers, json, timeout):
        calls["post"] = json
        return _FakeResponse(201)

    monkeypatch.setattr(github_sync.requests, "get", fake_get)
    monkeypatch.setattr(github_sync.requests, "post", fake_post)

    result = github_sync.ensure_branch_exists("me/repo", "tok", "data-snapshots", "main")

    assert result["success"] is True
    assert calls["post"] == {"ref": "refs/heads/data-snapshots", "sha": "main-head-sha"}


# --- get_file_content ----------------------------------------------------

def test_get_file_content_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(github_sync.requests, "get", lambda *a, **k: _FakeResponse(404))
    assert github_sync.get_file_content("me/repo", "tok", "path/file.jsonl", "data-snapshots") is None


def test_get_file_content_decodes_base64_and_returns_sha(monkeypatch):
    import base64

    encoded = base64.b64encode(b"hello world").decode("ascii")
    monkeypatch.setattr(
        github_sync.requests, "get", lambda *a, **k: _FakeResponse(200, {"content": encoded, "sha": "filesha"})
    )
    content, sha = github_sync.get_file_content("me/repo", "tok", "path/file.jsonl", "data-snapshots")
    assert content == b"hello world"
    assert sha == "filesha"
