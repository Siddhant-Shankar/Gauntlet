"""Connects to Temporal and builds the worker that runs Gauntlet's workflows and activities."""

from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from gauntlet.activities import AgentActivities, check_answer, load_schema
from gauntlet.agent import Agent
from gauntlet.workflows import SANDBOX_RUNNER, CaseWorkflow, RunWorkflow

TASK_QUEUE = "gauntlet"


async def connect(address: str) -> Client:
    return await Client.connect(address, data_converter=pydantic_data_converter)


def build_worker(
    client: Client, agent: Agent, executor: ThreadPoolExecutor, task_queue: str = TASK_QUEUE
) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[RunWorkflow, CaseWorkflow],
        activities=[load_schema, AgentActivities(agent).ask_agent, check_answer],
        activity_executor=executor,
        workflow_runner=SANDBOX_RUNNER,
    )
