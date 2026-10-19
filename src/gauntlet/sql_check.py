"""Judges one SQL answer by running it and the reference query, then comparing rows."""

import sqlite3
import time
from collections import Counter
from pathlib import Path

from gauntlet.models import Case, Failed, NeedsReview, Outcome, Passed

TIME_LIMIT_SECONDS = 5.0
PREVIEW_ROWS = 5

# First words of SQL statements. An answer that starts with anything else is prose.
SQL_FIRST_WORDS = frozenset(
    {"SELECT", "WITH", "VALUES", "INSERT", "UPDATE", "DELETE", "REPLACE"}
    | {"CREATE", "DROP", "ALTER", "ATTACH", "PRAGMA"}
)

type Row = tuple[object, ...]


class QueryTimeout(Exception):
    """A query ran longer than TIME_LIMIT_SECONDS."""


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
        conn.set_authorizer(_deny_attach)
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            cursor = conn.execute(sql)
            rows: list[Row] = cursor.fetchall()
        except sqlite3.OperationalError as error:
            if time.monotonic() > deadline:
                raise QueryTimeout from error
            raise
        return len(cursor.description or ()), rows
    finally:
        conn.close()


def _deny_attach(action: int, *_: str | None) -> int:
    # Read-only mode still allows ATTACH, which would let a query open other files.
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ATTACH else sqlite3.SQLITE_OK


def _looks_like_sql(answer: str) -> bool:
    words = answer.split(maxsplit=1)
    return bool(words) and words[0].upper() in SQL_FIRST_WORDS


def _columns(count: int) -> str:
    return f"{count} column" if count == 1 else f"{count} columns"


def _preview(rows: list[Row]) -> str:
    if len(rows) <= PREVIEW_ROWS:
        return repr(rows)
    shown = ", ".join(repr(row) for row in rows[:PREVIEW_ROWS])
    return f"[{shown}, ...] ({len(rows)} rows)"
