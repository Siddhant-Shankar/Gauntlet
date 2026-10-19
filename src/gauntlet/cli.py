"""Command line: build the demo database, start a worker, or run a suite and print the report."""

import argparse
import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from anthropic import Anthropic

from gauntlet.claude_agent import DEFAULT_MODEL, REQUEST_TIMEOUT_SECONDS, ClaudeAgent
from gauntlet.models import RunConfig, Suite
from gauntlet.report import format_report
from gauntlet.worker import TASK_QUEUE, build_worker, connect
from gauntlet.workflows import RunWorkflow

DEFAULT_ADDRESS = "localhost:7233"


def build_db(sql_path: Path, db_path: Path) -> None:
    db_path.unlink(missing_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(sql_path.read_text(encoding="utf-8"))


async def run_worker(address: str, model: str) -> None:
    client = await connect(address)
    agent = ClaudeAgent(Anthropic(timeout=REQUEST_TIMEOUT_SECONDS), model)
    with ThreadPoolExecutor() as executor:
        worker = build_worker(client, agent, executor)
        print(f"Worker ready on task queue '{TASK_QUEUE}' using {model}. Ctrl+C to stop.")
        await worker.run()


async def run_suite(
    address: str, suite: Suite, config: RunConfig, run_id: str, task_queue: str = TASK_QUEUE
) -> str:
    client = await connect(address)
    outcomes = await client.execute_workflow(
        RunWorkflow.run, args=[suite, config], id=run_id, task_queue=task_queue
    )
    return format_report(suite, config.k, outcomes)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "demo-db":
        build_db(args.sql, args.db)
    elif args.command == "worker":
        asyncio.run(run_worker(args.address, args.model))
    elif args.command == "run":
        suite = Suite.model_validate_json(args.suite.read_text(encoding="utf-8"))
        config = RunConfig(k=args.k, db_path=args.db.resolve())  # workers may run elsewhere
        run_id = f"{suite.name}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
        print(f"Started {run_id}; waiting for workers...", flush=True)
        print(asyncio.run(run_suite(args.address, suite, config, run_id)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gauntlet", description="A durable eval runner.")
    parser.add_argument("--address", default=DEFAULT_ADDRESS, help="Temporal server address")
    commands = parser.add_subparsers(dest="command", required=True)

    demo_db = commands.add_parser("demo-db", help="build a SQLite database from a SQL file")
    demo_db.add_argument("sql", type=Path)
    demo_db.add_argument("db", type=Path)

    worker = commands.add_parser("worker", help="run a worker that asks Claude")
    worker.add_argument("--model", default=DEFAULT_MODEL)

    run = commands.add_parser("run", help="run a suite and print the report")
    run.add_argument("suite", type=Path)
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("-k", type=int, default=5)
    return parser
