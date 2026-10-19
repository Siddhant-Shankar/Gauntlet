"""Gauntlet's workflows: a run starts one child workflow per case."""

import asyncio
from contextlib import suppress
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
    from gauntlet.models import (
        Attempt,
        Case,
        Errored,
        NeedsReview,
        Reviewed,
        RunConfig,
        Suite,
        Verdict,
    )

# pydantic imports annotated_types lazily, from inside the sandbox
SANDBOX_RUNNER = SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules("annotated_types")
)
AGENT_TIME_LIMIT = timedelta(minutes=2)  # must exceed the real agent's own HTTP timeout
HEARTBEAT_TIMEOUT = timedelta(seconds=3 * HEARTBEAT_EVERY_SECONDS)  # three missed beats
CHECK_TIME_LIMIT = timedelta(seconds=15)
REVIEW_DEADLINE = timedelta(days=7)
RETRIES = RetryPolicy(maximum_attempts=3)  # only timeouts reach this; activity errors are final


@workflow.defn
class CaseWorkflow:
    """Runs one case k times, then waits for a person to decide what no check could."""

    def __init__(self) -> None:
        self._attempts: list[Attempt] = []

    @workflow.run
    async def run(self, case: Case, config: RunConfig) -> list[Attempt]:
        schema = await workflow.execute_activity(
            load_schema,
            config.db_path,
            start_to_close_timeout=CHECK_TIME_LIMIT,
            retry_policy=RETRIES,
        )
        for number in range(1, config.k + 1):
            self._attempts.append(await self._attempt(number, case, config, schema))
        if self._review_pending():
            with suppress(TimeoutError):  # after the deadline, unreviewed attempts stay as they are
                await workflow.wait_condition(
                    lambda: not self._review_pending(), timeout=REVIEW_DEADLINE
                )
        return self._attempts

    @workflow.query
    def attempts(self) -> list[Attempt]:
        return self._attempts

    @workflow.update
    def review(self, verdict: Verdict) -> None:
        index = verdict.attempt - 1
        outcome = Reviewed(passed=verdict.passed, note=verdict.note)
        self._attempts[index] = self._attempts[index].model_copy(update={"outcome": outcome})

    @review.validator
    def _check_review(self, verdict: Verdict) -> None:
        number = verdict.attempt
        waiting = number <= len(self._attempts) and isinstance(
            self._attempts[number - 1].outcome, NeedsReview
        )
        if not waiting:
            raise ValueError(f"attempt {number} is not waiting for review")

    async def _attempt(self, number: int, case: Case, config: RunConfig, schema: str) -> Attempt:
        try:
            reply = await workflow.execute_activity_method(
                AgentActivities.ask_agent,
                args=[config.agent, case.prompt, schema],
                start_to_close_timeout=AGENT_TIME_LIMIT,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RETRIES,
            )
        except ActivityError as error:
            return Attempt(number=number, outcome=Errored(message=str(error.cause or error)))
        outcome = await workflow.execute_activity(
            check_answer,
            args=[case, reply, config.db_path],
            start_to_close_timeout=CHECK_TIME_LIMIT,
            retry_policy=RETRIES,
        )
        return Attempt(number=number, reply=reply, outcome=outcome)

    def _review_pending(self) -> bool:
        return any(isinstance(attempt.outcome, NeedsReview) for attempt in self._attempts)


@workflow.defn
class RunWorkflow:
    """Runs every case in a suite at the same time, each as a child workflow."""

    @workflow.run
    async def run(self, suite: Suite, config: RunConfig) -> dict[str, list[Attempt]]:
        parent_id = workflow.info().workflow_id
        results = await asyncio.gather(
            *(
                workflow.execute_child_workflow(
                    CaseWorkflow.run, args=[case, config], id=f"{parent_id}/{case.id}"
                )
                for case in suite.cases
            )
        )
        return {case.id: attempts for case, attempts in zip(suite.cases, results, strict=True)}
