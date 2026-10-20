"""The web interface: start runs, watch them live, and review what no check could decide."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from temporalio.client import (
    Client,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from gauntlet.claude_agent import DEFAULT_MODEL, MODELS
from gauntlet.models import Options, RunConfig, RunSummary, RunView, StartRequest, Suite, Verdict
from gauntlet.runs import load_run, new_run_id, recent_runs, review, start_run
from gauntlet.worker import TASK_QUEUE, connect

STATIC = Path(__file__).with_name("static")


def create_app(
    address: str,
    suites_dir: Path,
    db_path: Path,
    task_queue: str = TASK_QUEUE,
    agents: tuple[str, ...] = MODELS,
    default_agent: str = DEFAULT_MODEL,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.client = await connect(address)
        yield

    app = FastAPI(title="Gauntlet", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def temporal(request: Request) -> Client:
        client: Client = request.app.state.client
        return client

    def suites() -> dict[str, Suite]:
        loaded = (
            Suite.model_validate_json(path.read_text("utf-8")) for path in suites_dir.glob("*.json")
        )
        return {suite.name: suite for suite in loaded}

    @app.get("/")
    async def start_page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/runs/{run_id}")
    async def run_page(run_id: str) -> FileResponse:
        return FileResponse(STATIC / "run.html")

    @app.get("/compare")
    async def compare_page() -> FileResponse:
        return FileResponse(STATIC / "compare.html")

    @app.get("/api/options")
    async def options() -> Options:
        return Options(suites=tuple(sorted(suites())), agents=agents, default_agent=default_agent)

    @app.get("/api/runs")
    async def list_runs(request: Request) -> list[RunSummary]:
        return await recent_runs(temporal(request))

    @app.post("/api/runs")
    async def start_runs(start: StartRequest, request: Request) -> list[str]:
        suite = suites().get(start.suite)
        if suite is None:
            raise HTTPException(404, f"no suite named {start.suite!r}")
        unknown = [agent for agent in start.agents if agent not in agents]
        if unknown:
            raise HTTPException(400, f"unknown agents: {', '.join(unknown)}")
        run_ids = []
        for agent in dict.fromkeys(start.agents):  # each agent once, in the order given
            run_id = new_run_id(suite.name, agent)
            config = RunConfig(k=start.k, db_path=db_path, agent=agent)
            try:
                await start_run(temporal(request), suite, config, run_id, task_queue)
            except WorkflowAlreadyStartedError as error:
                raise HTTPException(409, f"{run_id} already exists; wait a second") from error
            run_ids.append(run_id)
        return run_ids

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> RunView:
        try:
            return await load_run(temporal(request), run_id)
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, f"no run named {run_id!r}") from error
            raise

    @app.post("/api/runs/{run_id}/cases/{case_id}/review")
    async def review_attempt(run_id: str, case_id: str, verdict: Verdict, request: Request) -> None:
        try:
            await review(temporal(request), run_id, case_id, verdict)
        except WorkflowUpdateFailedError as error:
            raise HTTPException(409, str(error.cause or error)) from error
        except WorkflowUpdateRPCTimeoutOrCancelledError as error:
            raise HTTPException(503, "no worker answered; is one running?") from error
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, f"no case {case_id!r} in run {run_id!r}") from error
            raise

    return app
