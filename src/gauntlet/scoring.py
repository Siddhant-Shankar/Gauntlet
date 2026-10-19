"""Turns each case's k outcomes into scores: passes all (pass^k) and passes some (pass@k)."""

from typing import assert_never

from gauntlet.models import (
    CaseScore,
    Errored,
    Failed,
    NeedsReview,
    Outcome,
    Passed,
    SuiteScore,
)


def score_case(case_id: str, outcomes: list[Outcome]) -> CaseScore:
    if not outcomes:
        raise ValueError(f"no outcomes to score for case {case_id}")

    passed = failed = errored = needs_review = 0
    for outcome in outcomes:
        if isinstance(outcome, Passed):
            passed += 1
        elif isinstance(outcome, Failed):
            failed += 1
        elif isinstance(outcome, Errored):
            errored += 1
        elif isinstance(outcome, NeedsReview):
            needs_review += 1
        else:
            assert_never(outcome)

    k = len(outcomes)
    return CaseScore(
        case_id=case_id,
        k=k,
        passed=passed,
        failed=failed,
        errored=errored,
        needs_review=needs_review,
        passes_all=passed == k,
        passes_some=passed > 0,
        pass_rate=passed / k,
    )


def score_suite(outcomes_by_case: dict[str, list[Outcome]]) -> SuiteScore:
    cases = tuple(score_case(case_id, outcomes) for case_id, outcomes in outcomes_by_case.items())
    return SuiteScore(
        cases=cases,
        passes_all=sum(case.passes_all for case in cases),
        passes_some=sum(case.passes_some for case in cases),
    )
