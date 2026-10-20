"""Start, read and review runs: what the CLI and the web page both need from Temporal."""

import asyncio
from datetime import UTC, datetime, timedelta

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from gauntlet.models import (
    Attempt,
    Case,
    CaseView,
    RunConfig,
    RunSummary,
    RunView,
    Suite,
    Verdict,
)
from gauntlet.scoring import score_case
from gauntlet.worker import TASK_QUEUE
from gauntlet.workflows import CaseWorkflow, RunWorkflow

QUERY_TIMEOUT = timedelta(seconds=2)  # a query waits for a worker; don't wait long for one
UPDATE_TIMEOUT = timedelta(seconds=10)


def new_run_id(suite_name: str, agent: str) -> str:
    return f"{suite_name}-{agent}-{datetime.now(UTC):%Y%m%d-%H%M%S}"


async def start_run(
    client: Client, suite: Suite, config: RunConfig, run_id: str, task_queue: str = TASK_QUEUE
) -> None:
    await client.start_workflow(
        RunWorkflow.run, args=[suite, config], id=run_id, task_queue=task_queue
    )


async def run_results(client: Client, run_id: str) -> dict[str, list[Attempt]]:
    handle = client.get_workflow_handle(run_id, result_type=dict[str, list[Attempt]])
    results: dict[str, list[Attempt]] = await handle.result()  # result() is typed Any
    return results


async def recent_runs(client: Client, limit: int = 20) -> list[RunSummary]:
    return [
        RunSummary(run_id=run.id, status=_status(run.status), started=run.start_time)
        async for run in client.list_workflows("WorkflowType = 'RunWorkflow'", limit=limit)
    ]


async def load_run(client: Client, run_id: str) -> RunView:
    handle = client.get_workflow_handle(run_id)
    description = await handle.describe()
    history = await handle.fetch_history()
    # The run's own inputs, read from its first event, so no worker is needed for them.
    started = history.events[0].workflow_execution_started_event_attributes
    suite, config = await client.data_converter.decode(started.input.payloads, [Suite, RunConfig])
    assert isinstance(suite, Suite) and isinstance(config, RunConfig)
    cases = await asyncio.gather(
        *(_load_case(client, run_id, case, config.k) for case in suite.cases)
    )
    return RunView(
        run_id=run_id,
        status=_status(description.status),
        suite=suite,
        config=config,
        cases=tuple(cases),
    )


async def review(client: Client, run_id: str, case_id: str, verdict: Verdict) -> None:
    handle = client.get_workflow_handle(f"{run_id}/{case_id}")
    await handle.execute_update(CaseWorkflow.review, verdict, rpc_timeout=UPDATE_TIMEOUT)


async def _load_case(client: Client, run_id: str, case: Case, k: int) -> CaseView:
    handle = client.get_workflow_handle(f"{run_id}/{case.id}", result_type=list[Attempt])
    try:
        status = (await handle.describe()).status
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        return _case_view(case, [], k)  # the run hasn't started this case yet
    if status == WorkflowExecutionStatus.COMPLETED:
        return _case_view(case, await handle.result(), k)
    if status == WorkflowExecutionStatus.RUNNING:
        try:
            attempts = await handle.query(CaseWorkflow.attempts, rpc_timeout=QUERY_TIMEOUT)
        except RPCError:
            return CaseView(case=case, attempts=None, score=None)  # no worker to answer
        return _case_view(case, attempts, k)
    return CaseView(case=case, attempts=None, score=None)  # failed or terminated


def _case_view(case: Case, attempts: list[Attempt], k: int) -> CaseView:
    outcomes = [attempt.outcome for attempt in attempts]
    score = score_case(case.id, outcomes) if len(attempts) == k else None
    return CaseView(case=case, attempts=tuple(attempts), score=score)


def _status(status: WorkflowExecutionStatus | None) -> str:
    return status.name if status is not None else "UNKNOWN"
