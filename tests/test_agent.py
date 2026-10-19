import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gauntlet.agent import Agent, FakeAgent, extract_sql, read_schema
from gauntlet.models import Case, Passed, SqlResult
from gauntlet.sql_check import check_sql

CUSTOMERS = "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT)"
ORDERS = "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER)"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(ORDERS)
        conn.execute(CUSTOMERS)
        conn.executemany(
            "INSERT INTO customers (name, country) VALUES (?, ?)",
            [("Ana", "USA"), ("Chloe", "Canada"), ("Ben", "USA"), ("Dev", "India")],
        )
        conn.commit()
    return path


# --- read_schema ---


def test_schema_lists_every_table_by_name(db_path: Path) -> None:
    assert read_schema(db_path) == f"{CUSTOMERS}\n\n{ORDERS}"


def test_schema_leaves_out_sqlite_internal_tables(db_path: Path) -> None:
    # AUTOINCREMENT on orders makes SQLite create its own sqlite_sequence table.
    assert "sqlite_sequence" not in read_schema(db_path)


def test_schema_of_a_missing_database_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        read_schema(missing)
    assert not missing.exists()


# --- extract_sql ---


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("  SELECT 1\n", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```SQL\nSELECT 1\n```", "SELECT 1"),
        ("```\nSELECT 1\n```", "SELECT 1"),
        ("```sql\r\nSELECT 1\r\n```", "SELECT 1"),
        ("```SELECT 1```", "SELECT 1"),
        ("Here you go:\n```sql\nSELECT 1\n```\nThis counts rows.", "SELECT 1"),
        ("```sql\nSELECT 1\n```\nor:\n```sql\nSELECT 2\n```", "SELECT 1"),
        ("```sql\nSELECT 1", "SELECT 1"),
        ("SELECT 1;\n```", "SELECT 1;"),
        ("There are 2 customers.", "There are 2 customers."),
    ],
    ids=[
        "plain sql",
        "whitespace",
        "sql fence",
        "uppercase tag",
        "bare fence",
        "windows newlines",
        "one-line fence",
        "prose around the fence",
        "first block wins",
        "reply cut off before closing fence",
        "only a closing fence, seen from Claude",
        "prose without a fence",
    ],
)
def test_extract_sql(reply: str, expected: str) -> None:
    assert extract_sql(reply) == expected


# --- FakeAgent ---


def test_fake_agent_fits_the_agent_shape() -> None:
    agent: Agent = FakeAgent({"q": ["SELECT 1"]})  # mypy rejects this line if the shapes differ
    assert agent.answer("q", "schema") == "SELECT 1"


def test_fake_agent_replies_in_order() -> None:
    agent = FakeAgent({"q": ["SELECT 1", "SELECT 2"]})
    assert agent.answer("q", "schema") == "SELECT 1"
    assert agent.answer("q", "schema") == "SELECT 2"


def test_fake_agent_keeps_a_separate_script_per_prompt() -> None:
    agent = FakeAgent({"q1": ["SELECT 1", "SELECT 2"], "q2": ["SELECT 3"]})
    assert agent.answer("q1", "schema") == "SELECT 1"
    assert agent.answer("q2", "schema") == "SELECT 3"
    assert agent.answer("q1", "schema") == "SELECT 2"


def test_fake_agent_raises_a_scripted_error() -> None:
    agent = FakeAgent({"q": [TimeoutError("agent timed out")]})
    with pytest.raises(TimeoutError, match="agent timed out"):
        agent.answer("q", "schema")


def test_fake_agent_out_of_replies_raises() -> None:
    agent = FakeAgent({"q": ["SELECT 1"]})
    agent.answer("q", "schema")
    with pytest.raises(LookupError, match="no reply left"):
        agent.answer("q", "schema")


def test_fake_agent_unknown_prompt_raises() -> None:
    with pytest.raises(LookupError, match="no reply left"):
        FakeAgent({"q": ["SELECT 1"]}).answer("other", "schema")


def test_fake_agent_leaves_the_callers_script_alone() -> None:
    replies: list[str | Exception] = ["SELECT 1"]
    FakeAgent({"q": replies}).answer("q", "schema")
    assert replies == ["SELECT 1"]


# --- one attempt, start to finish ---


def test_a_fenced_reply_passes_the_check_once_extracted(db_path: Path) -> None:
    usa_count = "SELECT COUNT(*) FROM customers WHERE country = 'USA'"
    case = Case(
        id="usa_customers",
        prompt="How many customers are in the USA?",
        check=SqlResult(reference_sql=usa_count),
    )
    agent = FakeAgent({case.prompt: [f"```sql\n{usa_count}\n```"]})
    reply = agent.answer(case.prompt, read_schema(db_path))
    assert check_sql(case, extract_sql(reply), db_path) == Passed()
