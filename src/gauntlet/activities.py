"""Temporal activities: the steps that do I/O, run on a worker's thread pool."""

from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from gauntlet.agent import Agent, extract_sql, read_schema
from gauntlet.models import Case, Outcome
from gauntlet.sql_check import check_sql

HEARTBEAT_EVERY_SECONDS = 2.0


@activity.defn
def load_schema(db_path: Path) -> str:
    try:
        return read_schema(db_path)
    except Exception as error:
        raise _final(error) from error


class AgentActivities:
    """The agent activity, bound to whichever agent the worker was started with."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    @activity.defn
    def ask_agent(self, prompt: str, schema: str) -> str:
        with ThreadPoolExecutor(max_workers=1) as pool:
            call = pool.submit(self._agent.answer, prompt, schema)
            while not call.done():
                activity.heartbeat()  # if this worker dies, the beats stop and Temporal retries
                wait([call], timeout=HEARTBEAT_EVERY_SECONDS)
        try:
            return call.result()
        except Exception as error:
            raise _final(error) from error  # the agent's own failure is this attempt's result


@activity.defn
def check_answer(case: Case, reply: str, db_path: Path) -> Outcome:
    try:
        return check_sql(case, extract_sql(reply), db_path)
    except Exception as error:
        raise _final(error) from error  # a broken reference query: a retry won't fix it


def _final(error: Exception) -> ApplicationError:
    """Wrap an error so Temporal won't retry it; only timeouts are retried."""
    return ApplicationError(f"{type(error).__name__}: {error}", non_retryable=True)
