import asyncio
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client

from gauntlet.agent import Agent, FakeAgent
from gauntlet.models import (
    Case,
    NeedsReview,
    Passed,
    Reviewed,
    RunConfig,
    RunView,
    SqlResult,
    Suite,
    Verdict,
)
from gauntlet.runs import load_run, new_run_id, review, run_results, start_run
from gauntlet.worker import build_worker

pytestmark = pytest.mark.asyncio(loop_scope="module")

USA_COUNT = "SELECT COUNT(*) FROM customers WHERE country = 'USA'"
CASE = Case(
    id="usa_customers",
    prompt="How many customers are in the USA?",
    check=SqlResult(reference_sql=USA_COUNT),
)
SUITE = Suite(name="mini", cases=(CASE,))


@asynccontextmanager
async def worker(client: Client, agent: Agent, task_queue: str) -> AsyncIterator[None]:
    with ThreadPoolExecutor() as executor:
        async with build_worker(client, {"fake": agent}, executor, task_queue):
            yield


async def start(client: Client, db_path: Path, k: int) -> tuple[str, str]:
    run_id, task_queue = f"run-{uuid4()}", f"test-{uuid4()}"
    config = RunConfig(k=k, db_path=db_path, agent="fake")
    await start_run(client, SUITE, config, run_id, task_queue)
    return run_id, task_queue


async def wait_for_view(client: Client, run_id: str, attempts: int) -> RunView:
    """Read the run until its one case shows `attempts` attempts."""
    while True:
        view = await load_run(client, run_id)
        case_attempts = view.cases[0].attempts
        if case_attempts is not None and len(case_attempts) >= attempts:
            return view
        await asyncio.sleep(0.1)


async def test_a_run_id_names_the_suite_and_agent() -> None:
    assert new_run_id("shop", "claude-haiku-5-5").startswith("shop-claude-haiku-5-5-20")


async def test_a_finished_run_reads_back_with_scores(client: Client, db_path: Path) -> None:
    run_id, task_queue = await start(client, db_path, k=2)
    async with worker(client, FakeAgent({CASE.prompt: [USA_COUNT] * 2}), task_queue):
        await run_results(client, run_id)
    view = await load_run(client, run_id)
    assert (view.status, view.suite, view.config.k, view.config.agent) == (
        "COMPLETED",
        SUITE,
        2,
        "fake",
    )
    [case] = view.cases
    assert case.attempts is not None
    assert [attempt.outcome for attempt in case.attempts] == [Passed(), Passed()]
    assert case.score is not None and case.score.passes_all


async def test_a_run_no_worker_has_picked_up_shows_no_attempts(
    client: Client, db_path: Path
) -> None:
    run_id, _ = await start(client, db_path, k=1)
    view = await load_run(client, run_id)
    assert view.status == "RUNNING"
    assert [(case.attempts, case.score) for case in view.cases] == [((), None)]


async def test_a_review_survives_a_worker_going_away(client: Client, db_path: Path) -> None:
    run_id, task_queue = await start(client, db_path, k=2)
    agent = FakeAgent({CASE.prompt: ["There are 2 customers in the USA.", USA_COUNT]})
    async with worker(client, agent, task_queue):
        view = await wait_for_view(client, run_id, attempts=2)
    attempts = view.cases[0].attempts
    assert attempts is not None
    assert attempts[0].outcome == NeedsReview(reason="no SQL in answer")

    view = await load_run(client, run_id)  # no worker now, so nobody can answer the query
    assert view.cases[0].attempts is None

    async with worker(client, FakeAgent({}), task_queue):
        await review(client, run_id, CASE.id, Verdict(attempt=1, passed=True, note="2 is right"))
        results = await run_results(client, run_id)
    outcomes = [attempt.outcome for attempt in results[CASE.id]]
    assert outcomes == [Reviewed(passed=True, note="2 is right"), Passed()]
