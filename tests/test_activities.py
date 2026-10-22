import time
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from gauntlet.activities import AgentActivities, check_answer, load_schema
from gauntlet.agent import FakeAgent
from gauntlet.models import Case, Passed, SqlResult

USA_COUNT = "SELECT COUNT(*) FROM customers WHERE country = 'USA'"


def make_case(reference_sql: str = USA_COUNT) -> Case:
    return Case(
        id="usa_customers",
        prompt="How many customers are in the USA?",
        check=SqlResult(reference_sql=reference_sql),
    )


class SlowAgent:
    def answer(self, prompt: str, schema: str) -> str:
        time.sleep(0.3)
        return "SELECT 1"


# --- load_schema ---


def test_load_schema_reads_the_tables(db_path: Path) -> None:
    assert load_schema(db_path).startswith("CREATE TABLE customers")


def test_a_missing_database_is_not_retried(tmp_path: Path) -> None:
    with pytest.raises(ApplicationError) as exc:
        load_schema(tmp_path / "missing.db")
    assert exc.value.non_retryable
    assert str(exc.value).startswith("OperationalError:")


# --- ask_agent ---


def test_ask_agent_returns_the_raw_reply() -> None:
    reply = f"```sql\n{USA_COUNT}\n```"
    agent = AgentActivities({"fake": FakeAgent({"q": [reply]})})
    assert ActivityEnvironment().run(agent.ask_agent, "fake", "q", "schema") == reply


def test_an_agent_error_is_not_retried() -> None:
    agent = AgentActivities({"fake": FakeAgent({"q": [TimeoutError("agent timed out")]})})
    with pytest.raises(ApplicationError) as exc:
        ActivityEnvironment().run(agent.ask_agent, "fake", "q", "schema")
    assert exc.value.non_retryable
    assert str(exc.value) == "TimeoutError: agent timed out"


def test_ask_agent_heartbeats_while_the_agent_thinks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gauntlet.activities.HEARTBEAT_EVERY_SECONDS", 0.05)
    env = ActivityEnvironment()
    beats: list[tuple[object, ...]] = []
    env.on_heartbeat = lambda *details: beats.append(details)
    agent = AgentActivities({"slow": SlowAgent()})
    assert env.run(agent.ask_agent, "slow", "q", "schema") == "SELECT 1"
    assert len(beats) >= 3


def test_an_agent_the_worker_does_not_have_is_not_retried() -> None:
    agent = AgentActivities({"fake": FakeAgent({"q": ["SELECT 1"]})})
    with pytest.raises(ApplicationError) as exc:
        ActivityEnvironment().run(agent.ask_agent, "claude-nope", "q", "schema")
    assert exc.value.non_retryable
    assert str(exc.value) == "no agent named 'claude-nope' on this worker"


# --- check_answer ---


def test_check_answer_strips_fences_before_checking(db_path: Path) -> None:
    assert check_answer(make_case(), f"```sql\n{USA_COUNT}\n```", db_path) == Passed()


def test_a_broken_reference_query_is_not_retried(db_path: Path) -> None:
    with pytest.raises(ApplicationError) as exc:
        check_answer(make_case("SELECT nope FROM customers"), USA_COUNT, db_path)
    assert exc.value.non_retryable
    assert str(exc.value).startswith("OperationalError:")
