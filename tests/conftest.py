import sqlite3
from collections.abc import AsyncIterator
from contextlib import closing
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "demo.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, country TEXT)")
        conn.executemany(
            "INSERT INTO customers (name, country) VALUES (?, ?)",
            [("Ana", "USA"), ("Chloe", "Canada"), ("Ben", "USA"), ("Dev", "India")],
        )
        conn.commit()
    return path


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[Client]:
    # A real local Temporal dev server, downloaded by the SDK on first use.
    async with await WorkflowEnvironment.start_local(data_converter=pydantic_data_converter) as env:
        yield env.client
