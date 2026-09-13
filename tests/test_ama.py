"""Unit tests for fantasy_football.ama - the Gemini orchestration layer.
No live Gemini calls (no GEMINI_API_KEY in this environment, and per
PROJECT_BRIEF testing requirements) - the Gemini client is monkeypatched
at the `ama._client` seam, and ama_query.run_readonly_query is
monkeypatched to avoid touching a real DB file. The one thing these
tests do NOT need to fake is the actual security boundary (ama_query.py)
- that has its own dedicated, thorough test suite in test_ama_query.py."""
import json

from fantasy_football import ama, ama_query


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, response_text):
        self.response_text = response_text
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.response_text)


class _FakeClient:
    def __init__(self, response_text):
        self.models = _FakeModels(response_text)


def test_schema_description_never_mentions_fantasypros():
    # the prompt Gemini sees must never even hint the table exists -
    # defense in depth alongside the authorizer's hard denial
    assert "fantasypros" not in ama.SCHEMA_DESCRIPTION.lower()


def test_generate_sql_extracts_sql_from_structured_response(monkeypatch):
    fake_client = _FakeClient(json.dumps({"sql": "SELECT * FROM ama_standings LIMIT 5"}))
    monkeypatch.setattr(ama, "_client", lambda api_key: fake_client)

    sql = ama.generate_sql("who's in first place?", {"season": 2025}, "fake-key", "gemini-2.5-flash")

    assert sql == "SELECT * FROM ama_standings LIMIT 5"
    call = fake_client.models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    assert "fantasypros" not in call["contents"].lower()
    assert "who's in first place?" in call["contents"]


def test_answer_question_grounds_on_supplied_rows_only(monkeypatch):
    fake_client = _FakeClient("Doody Guac Boys is in first place with a 12-2 record.")
    monkeypatch.setattr(ama, "_client", lambda api_key: fake_client)

    rows = [{"team_name": "Doody Guac Boys", "matchup_wins": 12, "matchup_losses": 2}]
    answer = ama.answer_question("who's in first place?", rows, "fake-key", "gemini-2.5-flash")

    assert answer == "Doody Guac Boys is in first place with a 12-2 record."
    call = fake_client.models.calls[0]
    assert "Doody Guac Boys" in call["contents"]
    assert "fantasypros" not in call["contents"].lower()


def test_ask_orchestrates_sql_generation_query_and_answer(monkeypatch):
    calls = {"sql_gen": 0, "query": 0, "answer": 0}

    def fake_generate_sql(question, context, api_key, model):
        calls["sql_gen"] += 1
        return "SELECT * FROM ama_standings"

    def fake_run_readonly_query(db_path, sql, max_rows=ama_query.MAX_ROWS):
        calls["query"] += 1
        assert sql == "SELECT * FROM ama_standings"
        return [{"team_name": "Test Team"}]

    def fake_answer_question(question, rows, api_key, model):
        calls["answer"] += 1
        assert rows == [{"team_name": "Test Team"}]
        return "Test Team is doing great."

    monkeypatch.setattr(ama, "generate_sql", fake_generate_sql)
    monkeypatch.setattr(ama_query, "run_readonly_query", fake_run_readonly_query)
    monkeypatch.setattr(ama, "answer_question", fake_answer_question)

    result = ama.ask("/fake/path.db", "how's my team doing?", {"season": 2025}, "fake-key", "gemini-2.5-flash")

    assert calls == {"sql_gen": 1, "query": 1, "answer": 1}
    assert result == {"answer": "Test Team is doing great.", "sql": "SELECT * FROM ama_standings", "row_count": 1}


def test_ask_propagates_ama_query_error_for_a_rejected_query(monkeypatch):
    monkeypatch.setattr(ama, "generate_sql", lambda *a, **k: "SELECT * FROM fantasypros_rankings")

    def fake_run_readonly_query(db_path, sql, max_rows=ama_query.MAX_ROWS):
        raise ama_query.AmaQueryError(ama_query.GENERIC_MESSAGE)

    monkeypatch.setattr(ama_query, "run_readonly_query", fake_run_readonly_query)

    import pytest

    with pytest.raises(ama_query.AmaQueryError):
        ama.ask("/fake/path.db", "show me fantasypros data", {}, "fake-key", "gemini-2.5-flash")
