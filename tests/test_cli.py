from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client

from gauntlet.agent import FakeAgent, read_schema
from gauntlet.cli import main, run_suite
from gauntlet.models import Case, RunConfig, SqlResult, Suite
from gauntlet.worker import build_worker

DEMO = Path(__file__).parents[1] / "demo"


def test_demo_db_builds_the_database(tmp_path: Path) -> None:
    db = tmp_path / "shop.db"
    main(["demo-db", str(DEMO / "shop.sql"), str(db)])
    assert read_schema(db).startswith("CREATE TABLE customers")


def test_demo_db_replaces_an_existing_file(tmp_path: Path) -> None:
    db = tmp_path / "shop.db"
    main(["demo-db", str(DEMO / "shop.sql"), str(db)])
    main(["demo-db", str(DEMO / "shop.sql"), str(db)])
    assert read_schema(db).count("CREATE TABLE customers") == 1


@pytest.mark.asyncio(loop_scope="module")
async def test_run_returns_the_report(client: Client, db_path: Path) -> None:
    count = "SELECT COUNT(*) FROM customers"
    suite = Suite(
        name="mini",
        cases=(
            Case(id="count_customers", prompt="How many?", check=SqlResult(reference_sql=count)),
        ),
    )
    task_queue = f"test-{uuid4()}"
    agent = FakeAgent({"How many?": [count, "SELECT 1"]})
    with ThreadPoolExecutor() as executor:
        async with build_worker(client, {"fake": agent}, executor, task_queue):
            report = await run_suite(
                client.service_client.config.target_host,
                suite,
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
