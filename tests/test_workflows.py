import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import (
    Client,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowUpdateFailedError,
)
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from gauntlet.agent import Agent, FakeAgent, read_schema
from gauntlet.models import (
    Attempt,
    Case,
    Errored,
    Failed,
    NeedsReview,
    Outcome,
    Passed,
    Reviewed,
    RunConfig,
    SqlResult,
    Suite,
    Verdict,
)
from gauntlet.worker import build_worker
from gauntlet.workflows import SANDBOX_RUNNER, CaseWorkflow, RunWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

USA_COUNT = "SELECT COUNT(*) FROM customers WHERE country = 'USA'"
ALL_COUNT = "SELECT COUNT(*) FROM customers"
PROSE = "There are 2 customers in the USA."
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


def config(db_path: Path, k: int) -> RunConfig:
    return RunConfig(k=k, db_path=db_path, agent="fake")


def outcomes_of(attempts: list[Attempt]) -> list[Outcome]:
    return [attempt.outcome for attempt in attempts]


@asynccontextmanager
async def worker(client: Client, agent: Agent) -> AsyncIterator[str]:
    """Run a worker on a task queue of its own, so tests can't pick up each other's work."""
    task_queue = f"test-{uuid4()}"
    with ThreadPoolExecutor() as executor:
        async with build_worker(client, {"fake": agent}, executor, task_queue):
            yield task_queue


async def run_case(
    client: Client, agent: Agent, db_path: Path, k: int, case: Case = CASE
) -> list[Attempt]:
    async with worker(client, agent) as task_queue:
        return await client.execute_workflow(
            CaseWorkflow.run,
            args=[case, config(db_path, k)],
            id=f"case-{uuid4()}",
            task_queue=task_queue,
        )


async def run_suite(
    client: Client, agent: Agent, db_path: Path, suite: Suite, run_id: str
) -> dict[str, list[Attempt]]:
    async with worker(client, agent) as task_queue:
        return await client.execute_workflow(
            RunWorkflow.run,
            args=[suite, config(db_path, 2)],
            id=run_id,
            task_queue=task_queue,
        )


async def wait_for_attempts(
    handle: WorkflowHandle[CaseWorkflow, list[Attempt]], count: int
) -> list[Attempt]:
    """Ask the running workflow for its attempts until it has made `count` of them."""
    while True:
        attempts = await handle.query(CaseWorkflow.attempts)
        if len(attempts) >= count:
            return attempts
        await asyncio.sleep(0.05)


class RecordingAgent:
    """Answers correctly and remembers what it was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def answer(self, prompt: str, schema: str) -> str:
        self.calls.append((prompt, schema))
        return USA_COUNT


# --- CaseWorkflow ---


async def test_each_attempt_keeps_its_reply_and_outcome(client: Client, db_path: Path) -> None:
    fenced = f"```sql\n{USA_COUNT}\n```"
    agent = FakeAgent({CASE.prompt: [fenced, ALL_COUNT]})
    assert await run_case(client, agent, db_path, k=2) == [
        Attempt(number=1, reply=fenced, outcome=Passed()),
        Attempt(number=2, reply=ALL_COUNT, outcome=Failed(reason="expected [(2,)], got [(4,)]")),
    ]


async def test_an_agent_error_is_errored_and_not_retried(client: Client, db_path: Path) -> None:
    # A retry would use up attempt 2's reply.
    agent = FakeAgent({CASE.prompt: [TimeoutError("agent timed out"), USA_COUNT]})
    assert await run_case(client, agent, db_path, k=2) == [
        Attempt(number=1, outcome=Errored(message="TimeoutError: agent timed out")),
        Attempt(number=2, reply=USA_COUNT, outcome=Passed()),
    ]


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
            args=[CASE, config(db_path, 2)],
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


# --- human review ---


async def test_a_person_decides_an_attempt_no_check_could(client: Client, db_path: Path) -> None:
    async with worker(client, FakeAgent({CASE.prompt: [PROSE, USA_COUNT]})) as task_queue:
        handle = await client.start_workflow(
            CaseWorkflow.run,
            args=[CASE, config(db_path, 2)],
            id=f"case-{uuid4()}",
            task_queue=task_queue,
        )
        attempts = await wait_for_attempts(handle, 2)
        assert attempts[0] == Attempt(
            number=1, reply=PROSE, outcome=NeedsReview(reason="no SQL in answer")
        )
        verdict = Verdict(attempt=1, passed=True, note="2 is right")
        await handle.execute_update(CaseWorkflow.review, verdict)
        result = await handle.result()
    assert outcomes_of(result) == [Reviewed(passed=True, note="2 is right"), Passed()]


async def test_only_an_attempt_waiting_for_review_can_be_reviewed(
    client: Client, db_path: Path
) -> None:
    async with worker(client, FakeAgent({CASE.prompt: [PROSE, USA_COUNT]})) as task_queue:
        handle = await client.start_workflow(
            CaseWorkflow.run,
            args=[CASE, config(db_path, 2)],
            id=f"case-{uuid4()}",
            task_queue=task_queue,
        )
        await wait_for_attempts(handle, 2)
        for number in [2, 3]:  # attempt 2 passed; there is no attempt 3
            with pytest.raises(WorkflowUpdateFailedError):
                verdict = Verdict(attempt=number, passed=False, note="x")
                await handle.execute_update(CaseWorkflow.review, verdict)
        verdict = Verdict(attempt=1, passed=False, note="prose, not SQL")
        await handle.execute_update(CaseWorkflow.review, verdict)
        result = await handle.result()
    assert outcomes_of(result) == [Reviewed(passed=False, note="prose, not SQL"), Passed()]


async def test_unreviewed_attempts_stay_needs_review_after_the_deadline(db_path: Path) -> None:
    # The time-skipping test server jumps over the 7-day wait instead of sleeping through it.
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        attempts = await run_case(env.client, FakeAgent({CASE.prompt: [PROSE]}), db_path, k=1)
    assert outcomes_of(attempts) == [NeedsReview(reason="no SQL in answer")]


# --- RunWorkflow ---


async def test_a_run_returns_every_cases_attempts(client: Client, db_path: Path) -> None:
    agent = FakeAgent({CASE.prompt: [USA_COUNT, ALL_COUNT], ALL_CASE.prompt: [ALL_COUNT] * 2})
    suite = Suite(name="demo", cases=(CASE, ALL_CASE))
    results = await run_suite(client, agent, db_path, suite, run_id=f"run-{uuid4()}")
    assert {case_id: outcomes_of(attempts) for case_id, attempts in results.items()} == {
        "usa_customers": [Passed(), Failed(reason="expected [(2,)], got [(4,)]")],
        "all_customers": [Passed(), Passed()],
    }


async def test_each_case_is_a_child_workflow_named_after_the_run(
    client: Client, db_path: Path
) -> None:
    run_id = f"run-{uuid4()}"
    suite = Suite(name="demo", cases=(CASE,))
    await run_suite(client, FakeAgent({CASE.prompt: [USA_COUNT] * 2}), db_path, suite, run_id)
    child = client.get_workflow_handle(f"{run_id}/usa_customers", result_type=list[Attempt])
    assert outcomes_of(await child.result()) == [Passed(), Passed()]


async def test_a_broken_case_fails_the_run(client: Client, db_path: Path) -> None:
    agent = FakeAgent({CASE.prompt: [USA_COUNT] * 2, "q": [USA_COUNT] * 2})
    suite = Suite(name="demo", cases=(CASE, BROKEN_CASE))
    with pytest.raises(WorkflowFailureError):
        await run_suite(client, agent, db_path, suite, run_id=f"run-{uuid4()}")
