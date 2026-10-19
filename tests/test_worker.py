import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHistory

from gauntlet.models import Case, Passed, RunConfig, SqlResult, Suite
from gauntlet.workflows import RunWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

CRASH_WORKER = Path(__file__).with_name("crash_worker.py")
CASES = {
    "usa_customers": (
        "How many customers are in the USA?",
        "SELECT COUNT(*) FROM customers WHERE country = 'USA'",
    ),
    "all_customers": ("How many customers are there?", "SELECT COUNT(*) FROM customers"),
}
SUITE = Suite(
    name="crash",
    cases=tuple(
        Case(id=case_id, prompt=prompt, check=SqlResult(reference_sql=sql))
        for case_id, (prompt, sql) in CASES.items()
    ),
)
ANSWERS = dict(CASES.values())


async def start_worker(client: Client, task_queue: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        str(CRASH_WORKER),
        client.service_client.config.target_host,
        task_queue,
        json.dumps(ANSWERS),
        stdout=asyncio.subprocess.PIPE,
    )


def ask_agent_tries(history: WorkflowHistory) -> list[int]:
    """The try number of each ask_agent activity in a workflow's history."""
    asks = {
        event.event_id
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        and event.activity_task_scheduled_event_attributes.activity_type.name == "ask_agent"
    }
    return [
        event.activity_task_started_event_attributes.attempt
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
        and event.activity_task_started_event_attributes.scheduled_event_id in asks
    ]


async def test_a_killed_worker_resumes_without_redoing_finished_attempts(
    client: Client, db_path: Path
) -> None:
    task_queue = f"test-{uuid4()}"
    run_id = f"run-{uuid4()}"
    worker_a = await start_worker(client, task_queue)
    workers = [worker_a]
    try:
        handle = await client.start_workflow(
            RunWorkflow.run,
            args=[SUITE, RunConfig(k=3, db_path=db_path)],
            id=run_id,
            task_queue=task_queue,
        )
        assert worker_a.stdout is not None
        for _ in CASES:
            assert (await worker_a.stdout.readline()).strip() == b"answered"
        await asyncio.sleep(0.2)  # both cases are now inside their second agent call
        worker_a.kill()
        await worker_a.wait()
        workers.append(await start_worker(client, task_queue))
        outcomes = await asyncio.wait_for(handle.result(), timeout=60)
    finally:
        for worker in workers:
            if worker.returncode is None:
                worker.kill()
                await worker.wait()

    assert outcomes == {case_id: [Passed()] * 3 for case_id in CASES}
    tries = [
        ask_agent_tries(await client.get_workflow_handle(f"{run_id}/{case_id}").fetch_history())
        for case_id in CASES
    ]
    assert [len(case_tries) for case_tries in tries] == [3, 3]  # finished attempts weren't redone
    assert max(max(case_tries) for case_tries in tries) == 2  # the cut-off call was retried once
