import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gauntlet import sql_check
from gauntlet.models import Case, Failed, NeedsReview, Passed, SqlResult
from gauntlet.sql_check import check_sql

COUNT = "SELECT COUNT(*) FROM customers"
COUNTRIES = "SELECT country FROM customers GROUP BY country"
TOP_TWO = "SELECT name FROM customers ORDER BY id DESC LIMIT 2"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT)")
        conn.executemany(
            "INSERT INTO customers (name, country) VALUES (?, ?)",
            [("Ana", "USA"), ("Chloe", "Canada"), ("Ben", "USA"), ("Dev", "India")],
        )
        conn.commit()
    return path


def make_case(reference_sql: str, ordered: bool = False) -> Case:
    return Case(
        id="question",
        prompt="a question",
        check=SqlResult(reference_sql=reference_sql, ordered=ordered),
    )


def reason_of(outcome: object) -> str:
    assert isinstance(outcome, Failed)
    return outcome.reason


# --- matching rows ---


def test_same_query_passes(db_path: Path) -> None:
    assert check_sql(make_case(COUNT), COUNT, db_path) == Passed()


def test_different_sql_with_the_same_rows_passes(db_path: Path) -> None:
    assert check_sql(make_case(COUNT), "SELECT COUNT(id) FROM customers", db_path) == Passed()


def test_column_names_are_ignored(db_path: Path) -> None:
    answer = "SELECT COUNT(*) AS how_many FROM customers"
    assert check_sql(make_case(COUNT), answer, db_path) == Passed()


def test_row_order_is_ignored_when_not_required(db_path: Path) -> None:
    answer = "SELECT DISTINCT country FROM customers"
    assert check_sql(make_case(COUNTRIES), answer, db_path) == Passed()


def test_query_starting_with_with_counts_as_sql(db_path: Path) -> None:
    answer = "WITH c AS (SELECT * FROM customers) SELECT COUNT(*) FROM c"
    assert check_sql(make_case(COUNT), answer, db_path) == Passed()


def test_two_empty_results_match(db_path: Path) -> None:
    empty = "SELECT name FROM customers WHERE country = 'Peru'"
    assert check_sql(make_case(empty), empty, db_path) == Passed()


# --- strict rules ---


def test_required_order_passes_when_it_matches(db_path: Path) -> None:
    assert check_sql(make_case(TOP_TWO, ordered=True), TOP_TWO, db_path) == Passed()


def test_required_order_fails_when_rows_are_reordered(db_path: Path) -> None:
    answer = "SELECT name FROM (SELECT * FROM customers ORDER BY id DESC LIMIT 2) ORDER BY id"
    outcome = check_sql(make_case(TOP_TWO, ordered=True), answer, db_path)
    assert reason_of(outcome) == "right rows, wrong order"


def test_duplicate_rows_are_counted(db_path: Path) -> None:
    answer = "SELECT country FROM customers"
    outcome = check_sql(make_case(COUNTRIES), answer, db_path)
    assert isinstance(outcome, Failed)


def test_extra_column_fails(db_path: Path) -> None:
    answer = "SELECT country, COUNT(*) FROM customers GROUP BY country"
    outcome = check_sql(make_case(COUNTRIES), answer, db_path)
    assert reason_of(outcome) == "expected 1 column, got 2"


def test_different_values_fail(db_path: Path) -> None:
    answer = "SELECT COUNT(*) - 1 FROM customers"
    outcome = check_sql(make_case(COUNT), answer, db_path)
    assert reason_of(outcome) == "expected [(4,)], got [(3,)]"


def test_long_results_are_shortened_in_the_reason(db_path: Path) -> None:
    ten = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 10) "
        "SELECT i FROM n"
    )
    eleven = ten.replace("i < 10", "i < 11")
    reason = reason_of(check_sql(make_case(ten), eleven, db_path))
    assert "...] (10 rows)" in reason
    assert "...] (11 rows)" in reason


# --- the agent's SQL is untrusted ---


def test_agent_sql_error_fails(db_path: Path) -> None:
    outcome = check_sql(make_case(COUNT), "SELECT COUNT(*) FROM customer", db_path)
    assert reason_of(outcome) == "agent SQL failed: no such table: customer"


@pytest.mark.parametrize("write", ["DELETE FROM customers", "DROP TABLE customers"])
def test_writes_are_refused(db_path: Path, write: str) -> None:
    outcome = check_sql(make_case(COUNT), write, db_path)
    assert reason_of(outcome) == "agent SQL failed: attempt to write a readonly database"
    assert check_sql(make_case(COUNT), COUNT, db_path) == Passed()  # nothing changed


def test_a_second_statement_is_refused(db_path: Path) -> None:
    outcome = check_sql(make_case(COUNT), "SELECT 1; DROP TABLE customers", db_path)
    assert "one statement at a time" in reason_of(outcome)


def test_attach_is_refused(db_path: Path) -> None:
    outcome = check_sql(make_case(COUNT), "ATTACH DATABASE ':memory:' AS other", db_path)
    assert reason_of(outcome) == "agent SQL failed: not authorized"


def test_runaway_query_hits_the_time_limit(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sql_check, "TIME_LIMIT_SECONDS", 0.2)
    runaway = (
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    )
    outcome = check_sql(make_case(COUNT), runaway, db_path)
    assert reason_of(outcome) == "query exceeded 0.2s"


# --- answers that aren't SQL, and broken suites ---


@pytest.mark.parametrize(
    "answer", ["Sorry, I can't tell from the data.", "Selection is unclear.", "", "   "]
)
def test_an_answer_that_is_not_sql_needs_review(db_path: Path, answer: str) -> None:
    assert check_sql(make_case(COUNT), answer, db_path) == NeedsReview(reason="no SQL in answer")


def test_broken_reference_sql_stops_the_run(db_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        check_sql(make_case("SELECT * FROM missing_table"), COUNT, db_path)


def test_missing_database_stops_the_run(tmp_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError):
        check_sql(make_case(COUNT), COUNT, tmp_path / "nope.db")
