"""Command line: build the demo database, start a worker, or run a suite and print the report."""

import argparse
import asyncio
import sqlite3
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing, suppress
from pathlib import Path

import uvicorn
from anthropic import Anthropic
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment

from gauntlet.agent import Agent
from gauntlet.claude_agent import DEFAULT_MODEL, MODELS, REQUEST_TIMEOUT_SECONDS, ClaudeAgent
from gauntlet.models import RunConfig, Suite
from gauntlet.report import format_report
from gauntlet.runs import new_run_id, run_results, start_run
from gauntlet.web import create_app
from gauntlet.worker import TASK_QUEUE, build_worker, connect

DEFAULT_ADDRESS = "localhost:7233"


def build_db(sql_path: Path, db_path: Path) -> None:
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(sql_path.read_text(encoding="utf-8"))


def claude_agents() -> dict[str, Agent]:
    """One agent per Claude model, sharing one API client that reads ANTHROPIC_API_KEY."""
    anthropic = Anthropic(timeout=REQUEST_TIMEOUT_SECONDS)
    return {model: ClaudeAgent(anthropic, model) for model in MODELS}


async def run_worker(address: str) -> None:
    client = await connect(address)
    with ThreadPoolExecutor() as executor:
        worker = build_worker(client, claude_agents(), executor)
        print(
            f"Worker ready on task queue '{TASK_QUEUE}' for {', '.join(MODELS)}. Ctrl+C to stop.",
            flush=True,
        )
        await worker.run()


@asynccontextmanager
async def temporal_and_worker(
    agents: dict[str, Agent], saved_runs: Path, port: int, ui_port: int | None
) -> AsyncIterator[str]:
    """Run a local Temporal server that saves runs to a file, plus a worker; yield its address."""
    # The SDK's dev server, the same one the tests use; it downloads itself on first use.
    server = await WorkflowEnvironment.start_local(
        data_converter=pydantic_data_converter,
        port=port,
        ui=ui_port is not None,
        ui_port=ui_port,
        dev_server_database_filename=str(saved_runs),
    )
    async with server:
        with ThreadPoolExecutor() as executor:
            async with build_worker(server.client, agents, executor):
                yield server.client.service_client.config.target_host


async def run_everything(
    suites_dir: Path, db_path: Path, port: int, temporal_port: int, saved_runs: Path
) -> None:
    agents = claude_agents()
    ui_port = temporal_port + 1000  # the Temporal CLI's own convention: 7233 -> 8233
    async with temporal_and_worker(agents, saved_runs, temporal_port, ui_port) as address:
        app = create_app(address, suites_dir, db_path, agents=tuple(agents))
        print(f"Gauntlet: http://127.0.0.1:{port}. Ctrl+C stops everything.", flush=True)
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port)).serve()


async def run_suite(
    address: str, suite: Suite, config: RunConfig, run_id: str, task_queue: str = TASK_QUEUE
) -> str:
    client = await connect(address)
    await start_run(client, suite, config, run_id, task_queue)
    results = await run_results(client, run_id)
    outcomes = {case_id: [a.outcome for a in attempts] for case_id, attempts in results.items()}
    return format_report(suite, config.k, outcomes)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "demo-db":
        build_db(args.sql, args.db)
    elif args.command == "worker":
        asyncio.run(run_worker(args.address))
    elif args.command == "run":
        suite = Suite.model_validate_json(args.suite.read_text(encoding="utf-8"))
        # An absolute path, because the workers may run in another folder or machine.
        config = RunConfig(k=args.k, db_path=args.db.resolve(), agent=args.agent)
        run_id = new_run_id(suite.name, args.agent)
        print(f"Started {run_id}; waiting for workers...", flush=True)
        print(asyncio.run(run_suite(args.address, suite, config, run_id)))
    elif args.command == "web":
        app = create_app(args.address, args.suites, args.db.resolve())
        uvicorn.run(app, host=args.host, port=args.port)
    elif args.command == "up":
        with suppress(KeyboardInterrupt):  # Ctrl+C is the normal way to stop it
            asyncio.run(
                run_everything(
                    args.suites, args.db.resolve(), args.port, args.temporal_port, args.temporal_db
                )
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gauntlet", description="A durable eval runner.")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="Temporal server address")
    commands = parser.add_subparsers(dest="command", required=True)

    demo_db = commands.add_parser("demo-db", help="build a SQLite database from a SQL file")
    demo_db.add_argument("sql", type=Path)
    demo_db.add_argument("db", type=Path)

    commands.add_parser("worker", help="run a worker that can ask any of the Claude models")

    run = commands.add_parser("run", help="run a suite and print the report")
    run.add_argument("suite", type=Path)
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("-k", type=int, default=5)
    run.add_argument("--agent", choices=MODELS, default=DEFAULT_MODEL)

    # Options both commands that serve the web app take.
    serving = argparse.ArgumentParser(add_help=False)
    serving.add_argument("--suites", type=Path, default=Path("demo"), help="folder of suite files")
    serving.add_argument("--db", type=Path, default=Path("demo/shop.db"))
    serving.add_argument("--port", type=int, default=8000, help="web app port")

    web = commands.add_parser(
        "web", parents=[serving], help="serve the web page: start, watch and review runs"
    )
    web.add_argument("--host", default="127.0.0.1")

    up = commands.add_parser(
        "up", parents=[serving], help="start Temporal, a worker and the web app, all at once"
    )
    up.add_argument("--temporal-port", type=int, default=7233)
    up.add_argument("--temporal-db", type=Path, default=Path("temporal.db"), help="saved runs")
    return parser
