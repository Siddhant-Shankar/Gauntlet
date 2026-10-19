import pytest

from gauntlet.models import CaseScore, Errored, Failed, NeedsReview, Outcome, Passed
from gauntlet.scoring import score_case, score_suite

PASS = Passed()
FAIL = Failed(reason="rows differ")
ERROR = Errored(message="timeout after 30s")
REVIEW = NeedsReview(reason="no SQL in answer")


def test_passing_every_attempt_passes_all_and_some() -> None:
    score = score_case("q", [PASS, PASS, PASS])
    assert score.passes_all is True
    assert score.passes_some is True
    assert score.pass_rate == 1.0


def test_passing_some_attempts_passes_some_but_not_all() -> None:
    score = score_case("q", [PASS, PASS, FAIL])
    assert score.passes_all is False
    assert score.passes_some is True
    assert score.pass_rate == pytest.approx(2 / 3)


def test_passing_no_attempts_passes_neither() -> None:
    score = score_case("q", [FAIL, FAIL, FAIL])
    assert score.passes_all is False
    assert score.passes_some is False
    assert score.pass_rate == 0.0


def test_an_errored_attempt_counts_as_not_passed() -> None:
    score = score_case("q", [PASS, PASS, ERROR])
    assert score.passes_all is False
    assert score.errored == 1


def test_an_attempt_needing_review_counts_as_not_passed() -> None:
    score = score_case("q", [PASS, PASS, REVIEW])
    assert score.passes_all is False
    assert score.needs_review == 1


def test_every_kind_is_counted() -> None:
    score = score_case("top_country", [PASS, REVIEW, ERROR, FAIL, PASS])
    assert score == CaseScore(
        case_id="top_country",
        k=5,
        passed=2,
        failed=1,
        errored=1,
        needs_review=1,
        passes_all=False,
        passes_some=True,
        pass_rate=0.4,
    )


def test_a_single_attempt_can_pass_all() -> None:
    score = score_case("q", [PASS])
    assert (score.k, score.passes_all, score.passes_some) == (1, True, True)


def test_no_attempts_cannot_be_scored() -> None:
    with pytest.raises(ValueError, match="no outcomes"):
        score_case("q", [])


def test_case_score_survives_a_json_round_trip() -> None:
    score = score_case("q", [PASS, FAIL, ERROR])
    assert CaseScore.model_validate_json(score.model_dump_json()) == score


def test_suite_counts_cases_that_pass_all_and_pass_some() -> None:
    outcomes: dict[str, list[Outcome]] = {
        "count_customers": [PASS, PASS, FAIL],
        "top_country": [PASS, REVIEW, ERROR],
        "newest_customer": [PASS, PASS, PASS],
        "busiest_month": [FAIL, ERROR, FAIL],
    }
    suite = score_suite(outcomes)
    assert [case.case_id for case in suite.cases] == list(outcomes)
    assert suite.passes_all == 1
    assert suite.passes_some == 3
    assert len(suite.cases) == 4
