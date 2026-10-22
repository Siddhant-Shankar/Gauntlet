import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from gauntlet.cli import build_db
from gauntlet.models import Case, Failed, Passed, Suite
from gauntlet.sql_check import check_sql

DEMO = Path(__file__).parents[1] / "demo"
SUITE = Suite.model_validate_json((DEMO / "suite.json").read_text(encoding="utf-8"))

# One plausible wrong query per hard case. A hard case only earns its place if its trap fails.
TRAPS = {
    # Forgets the status filter, so refunded and pending orders count as revenue.
    "running_revenue": (
        "WITH monthly AS (SELECT substr(o.order_date, 1, 7) AS month,"
        " SUM(oi.quantity * p.price_cents) AS revenue FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " GROUP BY month)"
        " SELECT month, revenue, SUM(revenue) OVER (ORDER BY month) FROM monthly ORDER BY month"
    ),
    # Keeps the first month, whose change is NULL.
    "revenue_change": (
        "WITH monthly AS (SELECT substr(o.order_date, 1, 7) AS month,"
        " SUM(oi.quantity * p.price_cents) AS revenue FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " WHERE o.status = 'paid' GROUP BY month)"
        " SELECT month, revenue - LAG(revenue) OVER (ORDER BY month) FROM monthly ORDER BY month"
    ),
    # Integer division: 100 * 13800 / 44600 is 30, not 30.9.
    "category_share": (
        "WITH shares AS (SELECT p.category, SUM(oi.quantity * p.price_cents) AS revenue"
        " FROM orders o JOIN order_items oi ON oi.order_id = o.id"
        " JOIN products p ON p.id = oi.product_id WHERE o.status = 'paid' GROUP BY p.category)"
        " SELECT category, ROUND(100 * revenue / (SELECT SUM(revenue) FROM shares), 1) FROM shares"
    ),
    # Averages item lines instead of whole orders.
    "avg_order_by_country": (
        "SELECT c.country, AVG(oi.quantity * p.price_cents) FROM orders o"
        " JOIN customers c ON c.id = o.customer_id JOIN order_items oi ON oi.order_id = o.id"
        " JOIN products p ON p.id = oi.product_id WHERE o.status = 'paid' GROUP BY c.country"
    ),
    # Compares each order with the average item line, not the average order.
    "above_average_orders": (
        "SELECT o.id, SUM(oi.quantity * p.price_cents) AS total FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " WHERE o.status = 'paid' GROUP BY o.id"
        " HAVING total > (SELECT AVG(oi.quantity * p.price_cents) FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " WHERE o.status = 'paid')"
    ),
    # The mean, not the median.
    "median_paid_order": (
        "WITH totals AS (SELECT SUM(oi.quantity * p.price_cents) AS total FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " WHERE o.status = 'paid' GROUP BY o.id)"
        " SELECT AVG(total) FROM totals"
    ),
    # Counts every order status.
    "top_product_per_category": (
        "WITH revenue AS (SELECT p.category, p.name, SUM(oi.quantity * p.price_cents) AS revenue"
        " FROM order_items oi JOIN products p ON p.id = oi.product_id GROUP BY p.id)"
        " SELECT category, name, MAX(revenue) FROM revenue GROUP BY category"
    ),
    # Any stationery product, not every one.
    "bought_all_stationery": (
        "SELECT DISTINCT c.name FROM customers c JOIN orders o ON o.customer_id = c.id"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " WHERE o.status = 'paid' AND p.category = 'stationery'"
    ),
    # The last order, not the second.
    "second_order_date": (
        "SELECT c.name, MAX(o.order_date) FROM customers c JOIN orders o ON o.customer_id = c.id"
        " GROUP BY c.id HAVING COUNT(*) >= 2"
    ),
    # An inner join drops June, where the only signup never paid.
    "quick_first_purchase": (
        "SELECT substr(c.signup_date, 1, 7) AS month, COUNT(DISTINCT c.id),"
        " COUNT(DISTINCT CASE WHEN julianday(o.order_date) - julianday(c.signup_date) <= 14"
        " THEN c.id END) FROM customers c"
        " JOIN orders o ON o.customer_id = c.id AND o.status = 'paid'"
        " GROUP BY month ORDER BY month"
    ),
    # Counts a customer who never paid as spending 0, which lowers Brazil's average.
    "above_country_average": (
        "WITH spend AS (SELECT c.name, c.country,"
        " TOTAL(CASE WHEN o.status = 'paid' THEN oi.quantity * p.price_cents END) AS spent"
        " FROM customers c LEFT JOIN orders o ON o.customer_id = c.id"
        " LEFT JOIN order_items oi ON oi.order_id = o.id"
        " LEFT JOIN products p ON p.id = oi.product_id GROUP BY c.id)"
        " SELECT name FROM spend s"
        " WHERE spent > (SELECT AVG(spent) FROM spend WHERE country = s.country)"
    ),
}

# Correct answers written differently from the reference: the check compares rows, not SQL.
ALTERNATIVES = {
    "median_paid_order": (
        "WITH totals AS (SELECT SUM(oi.quantity * p.price_cents) AS total FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id JOIN products p ON p.id = oi.product_id"
        " WHERE o.status = 'paid' GROUP BY o.id)"
        " SELECT AVG(total) FROM (SELECT total FROM totals ORDER BY total"
        " LIMIT 2 - (SELECT COUNT(*) FROM totals) % 2"
        " OFFSET ((SELECT COUNT(*) FROM totals) - 1) / 2)"
    ),
    "top_product_per_category": (
        "WITH revenue AS (SELECT p.category, p.name, SUM(oi.quantity * p.price_cents) AS revenue"
        " FROM orders o JOIN order_items oi ON oi.order_id = o.id"
        " JOIN products p ON p.id = oi.product_id WHERE o.status = 'paid' GROUP BY p.id)"
        " SELECT r.category, r.name, r.revenue FROM revenue r"
        " WHERE r.revenue = (SELECT MAX(revenue) FROM revenue WHERE category = r.category)"
    ),
    "bought_all_stationery": (
        "SELECT c.name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM products p"
        " WHERE p.category = 'stationery' AND NOT EXISTS (SELECT 1 FROM orders o"
        " JOIN order_items oi ON oi.order_id = o.id WHERE o.customer_id = c.id"
        " AND o.status = 'paid' AND oi.product_id = p.id))"
    ),
}


def case_named(case_id: str) -> Case:
    return next(case for case in SUITE.cases if case.id == case_id)


@pytest.fixture
def shop_db(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    build_db(DEMO / "shop.sql", path)
    return path


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


@pytest.mark.parametrize(("case_id", "sql"), list(TRAPS.items()), ids=list(TRAPS))
def test_a_common_mistake_fails_each_hard_case(case_id: str, sql: str, shop_db: Path) -> None:
    assert isinstance(check_sql(case_named(case_id), sql, shop_db), Failed)


@pytest.mark.parametrize(("case_id", "sql"), list(ALTERNATIVES.items()), ids=list(ALTERNATIVES))
def test_a_differently_written_answer_also_passes(case_id: str, sql: str, shop_db: Path) -> None:
    assert check_sql(case_named(case_id), sql, shop_db) == Passed()
