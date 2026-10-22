import socket
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client

from gauntlet.agent import Agent, FakeAgent, read_schema
from gauntlet.cli import main, run_suite, temporal_and_worker
from gauntlet.models import Case, RunConfig, SqlResult, Suite
from gauntlet.worker import build_worker

DEMO = Path(__file__).parents[1] / "demo"
COUNT = "SELECT COUNT(*) FROM customers"
MINI = Suite(
    name="mini",
    cases=(Case(id="count_customers", prompt="How many?", check=SqlResult(reference_sql=COUNT)),),
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def test_demo_db_builds_the_database(tmp_path: Path) -> None:
    db = tmp_path / "shop.db"
    main(["demo-db", str(DEMO / "shop.sql"), str(db)])
    assert read_schema(db).startswith("CREATE TABLE customers")


@pytest.mark.asyncio(loop_scope="module")
async def test_run_returns_the_report(client: Client, db_path: Path) -> None:
    task_queue = f"test-{uuid4()}"
    agent = FakeAgent({"How many?": [COUNT, "SELECT 1"]})
    with ThreadPoolExecutor() as executor:
        async with build_worker(client, {"fake": agent}, executor, task_queue):
            report = await run_suite(
                client.service_client.config.target_host,
                MINI,
                RunConfig(k=2, db_path=db_path, agent="fake"),
                run_id=f"run-{uuid4()}",
                task_queue=task_queue,
            )
    assert report.splitlines()[:5] == [
        "mini: k = 2, 1 cases",
        "",
        "case             passed  pass^2  pass@2",
        "count_customers  1/2     no      yes",
        "",
    ]


@pytest.mark.asyncio(loop_scope="module")
async def test_up_starts_its_own_temporal_with_a_worker(tmp_path: Path, db_path: Path) -> None:
    saved_runs = tmp_path / "temporal.db"
    agents: dict[str, Agent] = {"fake": FakeAgent({"How many?": [COUNT]})}
    async with temporal_and_worker(agents, saved_runs, free_port(), ui_port=None) as address:
        report = await run_suite(
            address, MINI, RunConfig(k=1, db_path=db_path, agent="fake"), run_id=f"run-{uuid4()}"
        )
    assert report.splitlines()[3].split() == ["count_customers", "1/1", "yes", "yes"]
    assert saved_runs.exists()
