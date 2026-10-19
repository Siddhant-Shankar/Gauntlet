from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from gauntlet.agent import Agent, FakeAgent, read_schema
from gauntlet.models import (
    Case,
    Errored,
    Failed,
    NeedsReview,
    Outcome,
    Passed,
    RunConfig,
    SqlResult,
    Suite,
)
from gauntlet.worker import build_worker
from gauntlet.workflows import SANDBOX_RUNNER, CaseWorkflow, RunWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

USA_COUNT = "SELECT COUNT(*) FROM customers WHERE country = 'USA'"
ALL_COUNT = "SELECT COUNT(*) FROM customers"
CASE = Case(
    id="usa_customers",
    prompt="How many customers are in the USA?",
    check=SqlResult(reference_sql=USA_COUNT),
)
ALL_CASE = Case(
    id="all_customers",
    prompt="How many customers are there?",
    check=SqlResult(reference_sql=ALL_COUNT),
)
BROKEN_CASE = Case(id="broken", prompt="q", check=SqlResult(reference_sql="SELECT nope FROM x"))


@asynccontextmanager
async def worker(client: Client, agent: Agent) -> AsyncIterator[str]:
    """Run a worker on a task queue of its own, so tests can't pick up each other's work."""
    task_queue = f"test-{uuid4()}"
    with ThreadPoolExecutor() as executor:
        async with build_worker(client, agent, executor, task_queue):
            yield task_queue


async def run_case(
    client: Client, agent: Agent, db_path: Path, k: int, case: Case = CASE
) -> list[Outcome]:
    async with worker(client, agent) as task_queue:
        return await client.execute_workflow(
            CaseWorkflow.run,
            args=[case, RunConfig(k=k, db_path=db_path)],
            id=f"case-{uuid4()}",
            task_queue=task_queue,
        )


async def run_suite(
    client: Client, agent: Agent, db_path: Path, suite: Suite, run_id: str
) -> dict[str, list[Outcome]]:
    async with worker(client, agent) as task_queue:
        return await client.execute_workflow(
            RunWorkflow.run,
            args=[suite, RunConfig(k=2, db_path=db_path)],
            id=run_id,
            task_queue=task_queue,
        )


class RecordingAgent:
    """Answers correctly and remembers what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def answer(self, prompt: str, schema: str) -> str:
        self.calls.append((prompt, schema))
        return USA_COUNT


# --- CaseWorkflow ---


async def test_each_attempt_gets_its_own_outcome(client: Client, db_path: Path) -> None:
    replies: list[str | Exception] = [
        f"```sql\n{USA_COUNT}\n```",
        ALL_COUNT,
        "There are 2 customers in the USA.",
    ]
    outcomes = await run_case(client, FakeAgent({CASE.prompt: replies}), db_path, k=3)
    assert outcomes == [
        Passed(),
        Failed(reason="expected [(2,)], got [(4,)]"),
        NeedsReview(reason="no SQL in answer"),
    ]


async def test_an_agent_error_is_errored_and_not_retried(client: Client, db_path: Path) -> None:
    # A retry would use up attempt 2's reply.
    agent = FakeAgent({CASE.prompt: [TimeoutError("agent timed out"), USA_COUNT]})
    outcomes = await run_case(client, agent, db_path, k=2)
    assert outcomes == [Errored(message="TimeoutError: agent timed out"), Passed()]


async def test_the_agent_gets_the_question_and_schema_only(client: Client, db_path: Path) -> None:
    agent = RecordingAgent()
    await run_case(client, agent, db_path, k=2)
    assert agent.calls == [(CASE.prompt, read_schema(db_path))] * 2


async def test_a_broken_reference_query_fails_the_workflow(client: Client, db_path: Path) -> None:
    with pytest.raises(WorkflowFailureError):
        await run_case(client, FakeAgent({"q": [USA_COUNT]}), db_path, k=1, case=BROKEN_CASE)


async def test_the_workflow_replays_from_its_history(client: Client, db_path: Path) -> None:
    async with worker(client, FakeAgent({CASE.prompt: [USA_COUNT, "SELECT 1"]})) as task_queue:
        handle = await client.start_workflow(
            CaseWorkflow.run,
            args=[CASE, RunConfig(k=2, db_path=db_path)],
            id=f"case-{uuid4()}",
            task_queue=task_queue,
        )
        await handle.result()
    # Reruns the workflow code against the saved history; fails if it now decides differently.
    replayer = Replayer(
        workflows=[CaseWorkflow],
        data_converter=pydantic_data_converter,
        workflow_runner=SANDBOX_RUNNER,
    )
    await replayer.replay_workflow(await handle.fetch_history())


# --- RunWorkflow ---


async def test_a_run_returns_every_cases_outcomes(client: Client, db_path: Path) -> None:
    agent = FakeAgent({CASE.prompt: [USA_COUNT, ALL_COUNT], ALL_CASE.prompt: [ALL_COUNT] * 2})
    suite = Suite(name="demo", cases=(CASE, ALL_CASE))
    outcomes = await run_suite(client, agent, db_path, suite, run_id=f"run-{uuid4()}")
    assert outcomes == {
        "usa_customers": [Passed(), Failed(reason="expected [(2,)], got [(4,)]")],
        "all_customers": [Passed(), Passed()],
    }


async def test_each_case_is_a_child_workflow_named_after_the_run(
    client: Client, db_path: Path
) -> None:
    run_id = f"run-{uuid4()}"
    suite = Suite(name="demo", cases=(CASE,))
    await run_suite(client, FakeAgent({CASE.prompt: [USA_COUNT] * 2}), db_path, suite, run_id)
    child = client.get_workflow_handle(f"{run_id}/usa_customers", result_type=list[Outcome])
    assert await child.result() == [Passed(), Passed()]


async def test_a_broken_case_fails_the_run(client: Client, db_path: Path) -> None:
    agent = FakeAgent({CASE.prompt: [USA_COUNT] * 2, "q": [USA_COUNT] * 2})
    suite = Suite(name="demo", cases=(CASE, BROKEN_CASE))
    with pytest.raises(WorkflowFailureError):
        await run_suite(client, agent, db_path, suite, run_id=f"run-{uuid4()}")
