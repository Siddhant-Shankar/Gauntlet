"""Judges one SQL answer by running it and the reference query, then comparing rows."""

import sqlite3
import time
from collections import Counter
from pathlib import Path

from gauntlet.models import Case, Failed, NeedsReview, Outcome, Passed

TIME_LIMIT_SECONDS = 5.0
MAX_VALUE_BYTES = 1_000_000
MAX_ROWS = 10_000
MAX_RESULT_BYTES = 10_000_000
PREVIEW_ROWS = 5
PREVIEW_CHARS = 100

# First words of SQL statements. An answer that starts with anything else is prose.
SQL_FIRST_WORDS = frozenset(
    {"SELECT", "WITH", "VALUES", "INSERT", "UPDATE", "DELETE", "REPLACE"}
    | {"CREATE", "DROP", "ALTER", "ATTACH", "PRAGMA"}
)

type Row = tuple[object, ...]


class QueryTimeout(Exception):
    """A query ran longer than TIME_LIMIT_SECONDS."""


class ResultTooLarge(Exception):
    """A query returned more than MAX_ROWS rows or MAX_RESULT_BYTES of text and blobs."""


def check_sql(case: Case, answer: str, db_path: Path) -> Outcome:
    """Judge the agent's SQL against the case's reference query."""
    if not _looks_like_sql(answer):
        return NeedsReview(reason="no SQL in answer")

    # A failing reference query is a bug in the suite, so it raises
    # instead of becoming an Outcome that counts against the agent.
    expected_width, expected = _run(case.check.reference_sql, db_path)
    try:
        width, got = _run(answer, db_path)
    except QueryTimeout:
        return Failed(reason=f"query exceeded {TIME_LIMIT_SECONDS:g}s")
    except ResultTooLarge:
        return Failed(reason=f"result too large: over {MAX_ROWS} rows or {MAX_RESULT_BYTES} bytes")
    except sqlite3.Error as error:
        return Failed(reason=f"agent SQL failed: {error}")

    if width != expected_width:
        return Failed(reason=f"expected {_columns(expected_width)}, got {width}")
    if Counter(got) != Counter(expected):
        return Failed(reason=f"expected {_preview(expected)}, got {_preview(got)}")
    if case.check.ordered and got != expected:
        return Failed(reason="right rows, wrong order")
    return Passed()


def _run(sql: str, db_path: Path) -> tuple[int, list[Row]]:
    """Run one statement on a read-only connection; return its column count and rows."""
    deadline = time.monotonic() + TIME_LIMIT_SECONDS
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        conn.set_authorizer(_deny_attach)
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            cursor = conn.execute(sql)
            rows = _fetch_capped(cursor)
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                raise QueryTimeout from error
            raise
        return len(cursor.description or ()), rows
    finally:
        conn.close()


def _fetch_capped(cursor: sqlite3.Cursor) -> list[Row]:
    rows: list[Row] = []
    size = 0
    for row in cursor:
        rows.append(row)
        size += sum(len(value) for value in row if isinstance(value, str | bytes))
        if len(rows) > MAX_ROWS or size > MAX_RESULT_BYTES:
            raise ResultTooLarge
    return rows


def _deny_attach(action: int, *_: str | None) -> int:
    # Read-only mode still allows ATTACH, which would let a query open other files.
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ATTACH else sqlite3.SQLITE_OK


def _looks_like_sql(answer: str) -> bool:
    words = answer.split(maxsplit=1)
    return bool(words) and words[0].upper() in SQL_FIRST_WORDS


def _columns(count: int) -> str:
    return f"{count} column" if count == 1 else f"{count} columns"


def _preview(rows: list[Row]) -> str:
    shown = ", ".join(_shorten(repr(row)) for row in rows[:PREVIEW_ROWS])
    if len(rows) > PREVIEW_ROWS:
        return f"[{shown}, ...] ({len(rows)} rows)"
    return f"[{shown}]"


def _shorten(text: str) -> str:
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "..."
