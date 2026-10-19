import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gauntlet.cli import build_db
from gauntlet.models import Case, Passed, Suite
from gauntlet.sql_check import check_sql

DEMO = Path(__file__).parents[1] / "demo"
SUITE = Suite.model_validate_json((DEMO / "suite.json").read_text(encoding="utf-8"))


@pytest.fixture
def shop_db(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    build_db(DEMO / "shop.sql", path)
    return path


def test_the_demo_suite_has_enough_cases() -> None:
    assert len(SUITE.cases) >= 10


@pytest.mark.parametrize("case", SUITE.cases, ids=lambda case: case.id)
def test_each_reference_query_passes_its_own_check(case: Case, shop_db: Path) -> None:
    assert check_sql(case, case.check.reference_sql, shop_db) == Passed()


@pytest.mark.parametrize("case", SUITE.cases, ids=lambda case: case.id)
def test_each_reference_query_returns_rows(case: Case, shop_db: Path) -> None:
    # An empty answer key would let an agent pass with any query that returns nothing.
    with closing(sqlite3.connect(shop_db)) as conn:
        assert conn.execute(case.check.reference_sql).fetchall()


def test_ordered_cases_say_how_to_order() -> None:
    for case in SUITE.cases:
        if case.check.ordered:
            assert "ORDER BY" in case.check.reference_sql
