"""Command line: build the demo database, start a worker, or run a suite and print the report."""

import argparse
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from anthropic import Anthropic

from gauntlet.agent import Agent
from gauntlet.claude_agent import DEFAULT_MODEL, MODELS, REQUEST_TIMEOUT_SECONDS, ClaudeAgent
from gauntlet.models import RunConfig, Suite
from gauntlet.report import format_report
from gauntlet.runs import run_results, start_run
from gauntlet.worker import TASK_QUEUE, build_worker, connect

DEFAULT_ADDRESS = "localhost:7233"


def build_db(sql_path: Path, db_path: Path) -> None:
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(sql_path.read_text(encoding="utf-8"))


async def run_worker(address: str) -> None:
    client = await connect(address)
    anthropic = Anthropic(timeout=REQUEST_TIMEOUT_SECONDS)
    agents: dict[str, Agent] = {model: ClaudeAgent(anthropic, model) for model in MODELS}
    with ThreadPoolExecutor() as executor:
        worker = build_worker(client, agents, executor)
        print(f"Worker ready on task queue '{TASK_QUEUE}' for {', '.join(MODELS)}. Ctrl+C to stop.")
        await worker.run()


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
        run_id = f"{suite.name}-{args.agent}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
        print(f"Started {run_id}; waiting for workers...", flush=True)
        print(asyncio.run(run_suite(args.address, suite, config, run_id)))


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
    return parser
