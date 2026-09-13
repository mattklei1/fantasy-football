"""Unit tests for fantasy_football.ama_query - the actual security
boundary for Ask Me Anything (see the module docstring). These tests are
the ones that matter most in this codebase: they prove
`fantasypros_rankings` is genuinely unreachable, not just discouraged by
a prompt, and that no write/multi-statement/injection attempt succeeds.
Uses a real on-disk sqlite file (tempfile) rather than :memory: since the
read-only enforcement is via a `file:...?mode=ro` URI, which needs an
actual file to point at."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from fantasy_football.ama_query import AmaQueryError, run_readonly_query


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE ama_public (id INTEGER, val TEXT);
            INSERT INTO ama_public VALUES (1, 'hello'), (2, 'world');
            CREATE TABLE fantasypros_rankings (id INTEGER, pos_rank INTEGER);
            INSERT INTO fantasypros_rankings VALUES (1, 5);
            """
        )
        conn.commit()
        conn.close()
        yield path


def test_legit_select_against_allowed_table_works(db_path):
    rows = run_readonly_query(db_path, "SELECT * FROM ama_public ORDER BY id")
    assert rows == [{"id": 1, "val": "hello"}, {"id": 2, "val": "world"}]


def test_cte_query_works(db_path):
    rows = run_readonly_query(db_path, "WITH x AS (SELECT * FROM ama_public) SELECT COUNT(*) AS n FROM x")
    assert rows == [{"n": 2}]


def test_fantasypros_table_is_unreachable(db_path):
    with pytest.raises(AmaQueryError):
        run_readonly_query(db_path, "SELECT * FROM fantasypros_rankings")


def test_fantasypros_table_unreachable_via_join(db_path):
    with pytest.raises(AmaQueryError):
        run_readonly_query(
            db_path, "SELECT a.id FROM ama_public a JOIN fantasypros_rankings f ON f.id = a.id"
        )


def test_fantasypros_table_unreachable_case_insensitive(db_path):
    with pytest.raises(AmaQueryError):
        run_readonly_query(db_path, "select * from FantasyPros_Rankings")


def test_fantasypros_table_unreachable_via_subquery(db_path):
    with pytest.raises(AmaQueryError):
        run_readonly_query(db_path, "SELECT (SELECT pos_rank FROM fantasypros_rankings LIMIT 1)")


def test_error_message_never_leaks_the_hidden_table_name(db_path):
    with pytest.raises(AmaQueryError) as exc_info:
        run_readonly_query(db_path, "SELECT * FROM fantasypros_rankings")
    assert "fantasypros" not in str(exc_info.value).lower()


def test_error_message_is_identical_whether_denied_or_just_malformed(db_path):
    # a distinguishable error message would itself leak information about
    # what's denied vs. merely broken - both must read the same to a caller
    try:
        run_readonly_query(db_path, "SELECT * FROM fantasypros_rankings")
    except AmaQueryError as e:
        denied_message = str(e)
    try:
        run_readonly_query(db_path, "SELECT * FROM this_table_does_not_exist_at_all")
    except AmaQueryError as e:
        malformed_message = str(e)
    assert denied_message == malformed_message


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ama_public",
        "UPDATE ama_public SET val = 'x'",
        "DROP TABLE ama_public",
        "INSERT INTO ama_public VALUES (3, 'z')",
        "ALTER TABLE ama_public ADD COLUMN z TEXT",
        "PRAGMA table_info(ama_public)",
        "ATTACH DATABASE ':memory:' AS other",
        "CREATE TABLE evil (id INTEGER)",
        "VACUUM",
    ],
)
def test_write_and_schema_operations_are_all_denied(db_path, sql):
    with pytest.raises(AmaQueryError):
        run_readonly_query(db_path, sql)


def test_stacked_statement_injection_is_rejected(db_path):
    with pytest.raises(AmaQueryError):
        run_readonly_query(db_path, "SELECT * FROM ama_public; DROP TABLE ama_public")


def test_trailing_semicolon_alone_is_fine(db_path):
    rows = run_readonly_query(db_path, "SELECT * FROM ama_public ORDER BY id;")
    assert len(rows) == 2


def test_empty_query_is_rejected(db_path):
    with pytest.raises(AmaQueryError):
        run_readonly_query(db_path, "   ")


def test_limit_is_auto_injected_when_missing(db_path):
    conn = sqlite3.connect(db_path)
    conn.executemany("INSERT INTO ama_public VALUES (?, ?)", [(i, str(i)) for i in range(3, 500)])
    conn.commit()
    conn.close()
    rows = run_readonly_query(db_path, "SELECT * FROM ama_public", max_rows=10)
    assert len(rows) == 10


def test_existing_limit_is_respected(db_path):
    rows = run_readonly_query(db_path, "SELECT * FROM ama_public ORDER BY id LIMIT 1")
    assert len(rows) == 1


def test_connection_is_genuinely_read_only_even_if_authorizer_were_bypassed(db_path):
    # belt-and-suspenders check on the read-only URI layer itself, not
    # just the authorizer - open the same way ama_query does and confirm
    # a raw write attempt at the sqlite3 layer fails regardless of any
    # authorizer logic
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO ama_public VALUES (999, 'x')")
        conn.commit()
    conn.close()
