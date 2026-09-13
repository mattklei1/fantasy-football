"""Ask Me Anything's sandboxed SQL execution layer. This is the actual
security boundary that keeps FantasyPros rankings (and anything else not
meant for league members) unreachable - NOT just a prompt instruction
telling Gemini to behave. Three independent, defense-in-depth layers:

1. Text-level validation (_validate_sql_text) - single read-only
   statement, no semicolon-chaining, no denylisted keywords.
2. The SQLite connection itself is opened READ-ONLY via a `mode=ro` URI
   - no write can succeed on this connection regardless of what SQL text
   reaches it, even if layers 1/3 both had a bug.
3. sqlite3.Connection.set_authorizer() - SQLite's own query-compilation-
   time access control callback. Denies SQLITE_READ on
   `fantasypros_rankings` specifically (the one table this app's owner
   explicitly doesn't want exposed) and denies every non-SELECT action
   (INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/PRAGMA/CREATE/TRANSACTION/
   etc.) outright. This is real SQLite-engine-level enforcement, checked
   and confirmed working in this exact environment (Python's sqlite3
   authorizer API) before being relied on here - not assumed from docs.

None of this DB ever stores API keys or secrets (those live only in
.env, never ingested into any table), so the "don't leak the FantasyPros/
GroupMe keys" half of the requirement is automatically satisfied by the
DB simply never containing them - the FantasyPros RANKINGS DATA (the
thing worth protecting) is what layers 1-3 above exist to block.

Error messages returned to the caller are always a fixed, generic string
- never a raw sqlite3 exception - because the authorizer's own denial
message ("access to fantasypros_rankings.foo is prohibited") would
itself leak the existence/name of the hidden table, which defeats the
point.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# The one table Ask Me Anything must never be able to read. Everything
# else in the DB is fair game (default-allow) - deny-listing this single
# table is more robust than an allowlist that has to be kept perfectly in
# sync with the schema by hand.
DENIED_TABLES = {"fantasypros_rankings"}

MAX_ROWS = 200

_DISALLOWED_KEYWORDS = re.compile(
    r"\b(attach|detach|pragma|insert|update|delete|drop|alter|create|replace|vacuum|reindex|analyze)\b",
    re.IGNORECASE,
)


class AmaQueryError(ValueError):
    """Always raised with GENERIC_MESSAGE - see that constant's docstring
    for why every rejection path (malformed query, denied table, a
    genuine SQL error) must be indistinguishable to the caller."""


# Every single rejection in this module - a malformed query, a denied
# table, a SQL syntax error, a nonexistent table - raises with this EXACT
# same string. A single differentiated message (e.g. "only SELECT is
# allowed" vs. "that table doesn't exist") would let a curious prober
# learn something by comparing responses across attempts; a uniform
# surface leaks nothing regardless of how the code evolves later.
GENERIC_MESSAGE = "That question couldn't be answered with the data available."


def _validate_sql_text(sql: str) -> str:
    text = sql.strip()
    if not text:
        raise AmaQueryError(GENERIC_MESSAGE)

    # allow one optional trailing semicolon, then require there be no
    # more - blocks stacked-statement injection before it ever reaches
    # sqlite3 (which itself also rejects multi-statement .execute() calls,
    # but don't rely on that alone)
    text = text.rstrip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    if ";" in text:
        raise AmaQueryError(GENERIC_MESSAGE)

    first_word = text.split(None, 1)[0].lower() if text.split(None, 1) else ""
    if first_word not in ("select", "with"):
        raise AmaQueryError(GENERIC_MESSAGE)

    if _DISALLOWED_KEYWORDS.search(text):
        raise AmaQueryError(GENERIC_MESSAGE)

    for denied in DENIED_TABLES:
        if denied.lower() in text.lower():
            raise AmaQueryError(GENERIC_MESSAGE)

    return text


def _enforce_limit(sql: str, max_rows: int) -> str:
    if re.search(r"\blimit\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"{sql}\nLIMIT {max_rows}"


def _authorizer(action, arg1, arg2, db_name, trigger_name):  # noqa: ANN001 - sqlite3 callback signature
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_DENY if arg1 in DENIED_TABLES else sqlite3.SQLITE_OK
    # every other action - INSERT/UPDATE/DELETE/DROP/ALTER/ATTACH/
    # PRAGMA/CREATE/TRANSACTION/etc. - denied by default (fail closed)
    return sqlite3.SQLITE_DENY


def run_readonly_query(db_path: Path | str, sql: str, max_rows: int = MAX_ROWS) -> list[dict]:
    """Validates and runs ONE read-only SELECT against a fresh, sandboxed
    connection. Raises AmaQueryError (safe to display) on any rejection
    or failure - never leaks a raw sqlite3 error message, which could
    itself reveal a denied table's name."""
    validated = _validate_sql_text(sql)
    limited = _enforce_limit(validated, max_rows)

    conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.set_authorizer(_authorizer)
        cursor = conn.execute(limited)
        rows = cursor.fetchmany(max_rows)
        return [dict(r) for r in rows]
    except sqlite3.DatabaseError:
        # covers authorizer denials AND genuine SQL errors alike - see
        # GENERIC_MESSAGE's docstring for why these must be indistinguishable
        raise AmaQueryError(GENERIC_MESSAGE) from None
    finally:
        conn.close()
