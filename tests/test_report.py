from gauntlet.models import (
    Case,
    Errored,
    Failed,
    NeedsReview,
    Outcome,
    Passed,
    SqlResult,
    Suite,
)
from gauntlet.report import format_report

SUITE = Suite(
    name="shop",
    cases=(
        Case(id="count_customers", prompt="q", check=SqlResult(reference_sql="SELECT 1")),
        Case(id="top_3_products", prompt="q", check=SqlResult(reference_sql="SELECT 1")),
    ),
)


def test_report_shows_scores_and_every_problem() -> None:
    outcomes: dict[str, list[Outcome]] = {
        "count_customers": [Passed(), Passed(), Passed()],
        "top_3_products": [
            NeedsReview(reason="no SQL in answer"),
            Failed(reason="right rows, wrong order"),
            Errored(message="TimeoutError: agent timed out"),
        ],
    }
    assert format_report(SUITE, 3, outcomes) == "\n".join(
        [
            "shop: k = 3, 2 cases",
            "",
            "case             passed  pass^3  pass@3",
            "count_customers  3/3     yes     yes",
            "top_3_products   0/3     no      no",
            "",
            "pass^3: 1 of 2 cases, pass@3: 1 of 2 cases",
            "",
            "Needs review",
            "  top_3_products #1: no SQL in answer",
            "",
            "Failed",
            "  top_3_products #2: right rows, wrong order",
            "",
            "Errored",
            "  top_3_products #3: TimeoutError: agent timed out",
        ]
    )


def test_a_clean_run_lists_no_problems() -> None:
    outcomes: dict[str, list[Outcome]] = {
        "count_customers": [Passed()],
        "top_3_products": [Passed()],
    }
    report = format_report(SUITE, 1, outcomes)
    assert report.endswith("pass^1: 2 of 2 cases, pass@1: 2 of 2 cases")
