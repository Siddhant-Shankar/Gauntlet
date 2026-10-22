import asyncio
import http.client
import json
import socket
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import uvicorn
from temporalio.client import Client

from gauntlet.cli import build_db
from gauntlet.models import Suite
from gauntlet.web import create_app
from gauntlet.worker import build_worker

pytestmark = pytest.mark.asyncio(loop_scope="module")

DEMO = Path(__file__).parents[1] / "demo"
SUITE = Suite.model_validate_json((DEMO / "suite.json").read_text(encoding="utf-8"))
PROSE_CASE = "paid_orders"
PROSE = "Ten orders have been paid."


class AnswerKeyAgent:
    """Answers every demo question with its reference SQL, except one it answers in prose."""

    def __init__(self) -> None:
        self._answers = {case.prompt: case.check.reference_sql for case in SUITE.cases}
        self._prose = next(case.prompt for case in SUITE.cases if case.id == PROSE_CASE)

    def answer(self, prompt: str, schema: str) -> str:
        return PROSE if prompt == self._prose else self._answers[prompt]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def site(client: Client, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[int]:
    """Serve the web app on a free port, with a worker behind it; yields the port."""
    db = tmp_path_factory.mktemp("web") / "shop.db"
    build_db(DEMO / "shop.sql", db)
    address = client.service_client.config.target_host
    app = create_app(
        address, DEMO, db, task_queue="web-test", agents=("fake",), default_agent="fake"
    )
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    for _ in range(200):  # up to 10 s for the server to come up
        if server.started:
            break
        await asyncio.sleep(0.05)
    with ThreadPoolExecutor() as executor:
        async with build_worker(client, {"fake": AnswerKeyAgent()}, executor, "web-test"):
            yield port
    server.should_exit = True
    await serving


def send(port: int, method: str, path: str, body: object | None = None) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        payload = None if body is None else json.dumps(body)
        headers = {"Content-Type": "application/json"}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


async def call(port: int, method: str, path: str, body: object | None = None) -> tuple[int, Any]:
    # Any: the decoded JSON body, in whatever shape the route returned.
    status, raw = await asyncio.to_thread(send, port, method, path, body)
    return status, json.loads(raw) if raw else None


async def wait_for_run(port: int, run_id: str, ready: Callable[[Any], bool]) -> Any:
    for _ in range(300):
        _, view = await call(port, "GET", f"/api/runs/{run_id}")
        if ready(view):
            return view
        await asyncio.sleep(0.1)
    pytest.fail(f"{run_id} never got there")


def case_of(view: Any, case_id: str) -> Any:
    return next(case for case in view["cases"] if case["case"]["id"] == case_id)


async def test_the_pages_are_served(site: int) -> None:
    for path in ["/", "/runs/any-run", "/compare", "/static/app.js", "/static/app.css"]:
        status, _ = await asyncio.to_thread(send, site, "GET", path)
        assert status == 200, path


async def test_the_options_list_the_suites_and_agents(site: int) -> None:
    assert await call(site, "GET", "/api/options") == (
        200,
        {"suites": ["shop"], "agents": ["fake"], "default_agent": "fake"},
    )


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        ("POST", "/api/runs", {"suite": "nope", "k": 1, "agents": ["fake"]}, 404),
        ("POST", "/api/runs", {"suite": "shop", "k": 1, "agents": ["gpt"]}, 400),
        ("POST", "/api/runs", {"suite": "shop", "k": 21, "agents": ["fake"]}, 422),
        ("GET", "/api/runs/no-such-run", None, 404),
    ],
)
async def test_bad_requests_are_refused(
    site: int, method: str, path: str, body: object | None, expected: int
) -> None:
    status, _ = await call(site, method, path, body)
    assert status == expected


async def test_start_watch_and_review_a_run(site: int) -> None:
    status, run_ids = await call(
        site, "POST", "/api/runs", {"suite": "shop", "k": 1, "agents": ["fake"]}
    )
    assert status == 200
    [run_id] = run_ids

    view = await wait_for_run(site, run_id, lambda v: bool(case_of(v, PROSE_CASE)["attempts"]))
    [attempt] = case_of(view, PROSE_CASE)["attempts"]
    assert (attempt["reply"], attempt["outcome"]["kind"]) == (PROSE, "needs_review")

    review_path = f"/api/runs/{run_id}/cases/{PROSE_CASE}/review"
    wrong = {"attempt": 2, "passed": True, "note": "there is no attempt 2"}
    assert (await call(site, "POST", review_path, wrong))[0] == 409
    verdict = {"attempt": 1, "passed": True, "note": "10 is the right count"}
    assert (await call(site, "POST", review_path, verdict))[0] == 200

    view = await wait_for_run(site, run_id, lambda v: v["status"] == "COMPLETED")
    kinds = {case["case"]["id"]: case["attempts"][0]["outcome"]["kind"] for case in view["cases"]}
    assert kinds == {
        case.id: "reviewed" if case.id == PROSE_CASE else "passed" for case in SUITE.cases
    }
    assert case_of(view, PROSE_CASE)["score"]["passes_all"] is True

    for _ in range(50):  # the run list is eventually consistent
        _, runs = await call(site, "GET", "/api/runs")
        if run_id in [run["run_id"] for run in runs]:
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"{run_id} never appeared in the run list")
