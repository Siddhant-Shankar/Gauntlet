"""Gauntlet's workflows: a run starts one child workflow per case."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

# One copy of these classes inside and outside the sandbox, so isinstance checks hold.
with workflow.unsafe.imports_passed_through():
    from gauntlet.activities import (
        HEARTBEAT_EVERY_SECONDS,
        AgentActivities,
        check_answer,
        load_schema,
    )
    from gauntlet.models import Case, Errored, Outcome, RunConfig, Suite

# pydantic imports annotated_types lazily, from inside the sandbox
SANDBOX_RUNNER = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules("annotated_types")
)
AGENT_TIME_LIMIT = timedelta(minutes=2)  # must exceed the real agent's own HTTP timeout
HEARTBEAT_TIMEOUT = timedelta(seconds=3 * HEARTBEAT_EVERY_SECONDS)  # three missed beats
CHECK_TIME_LIMIT = timedelta(seconds=15)
RETRIES = RetryPolicy(maximum_attempts=3)  # only timeouts reach this; activity errors are final


@workflow.defn
class CaseWorkflow:
    """Runs one case k times and returns one Outcome per attempt."""

    @workflow.run
    async def run(self, case: Case, config: RunConfig) -> list[Outcome]:
        schema = await workflow.execute_activity(
            load_schema,
            config.db_path,
            start_to_close_timeout=CHECK_TIME_LIMIT,
            retry_policy=RETRIES,
        )
        outcomes: list[Outcome] = []
        for _ in range(config.k):
            try:
                reply = await workflow.execute_activity_method(
                    AgentActivities.ask_agent,
                    args=[case.prompt, schema],
                    start_to_close_timeout=AGENT_TIME_LIMIT,
                    heartbeat_timeout=HEARTBEAT_TIMEOUT,
                    retry_policy=RETRIES,
                )
            except ActivityError as error:
                outcomes.append(Errored(message=str(error.cause or error)))
                continue
            outcome = await workflow.execute_activity(
                check_answer,
                args=[case, reply, config.db_path],
                start_to_close_timeout=CHECK_TIME_LIMIT,
                retry_policy=RETRIES,
            )
            outcomes.append(outcome)
        return outcomes


@workflow.defn
class RunWorkflow:
    """Runs every case in a suite at the same time, each as a child workflow."""

    @workflow.run
    async def run(self, suite: Suite, config: RunConfig) -> dict[str, list[Outcome]]:
        parent_id = workflow.info().workflow_id
        results = await asyncio.gather(
            *(
                workflow.execute_child_workflow(
                    CaseWorkflow.run, args=[case, config], id=f"{parent_id}/{case.id}"
                )
                for case in suite.cases
            )
        )
        return {case.id: outcomes for case, outcomes in zip(suite.cases, results, strict=True)}
