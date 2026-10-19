"""Formats a finished run as a plain-text report."""

from typing import assert_never

from gauntlet.models import Errored, Failed, NeedsReview, Outcome, Passed, Reviewed, Suite
from gauntlet.scoring import score_suite


def format_report(suite: Suite, k: int, outcomes_by_case: dict[str, list[Outcome]]) -> str:
    score = score_suite(outcomes_by_case)
    total = len(score.cases)
    width = max(len("case"), *(len(case.case_id) for case in score.cases))
    lines = [
        f"{suite.name}: k = {k}, {total} cases",
        "",
        f"{'case':<{width}}  passed  pass^{k}  pass@{k}",
    ]
    for case in score.cases:
        passed = f"{case.passed}/{case.k}"
        lines.append(
            f"{case.case_id:<{width}}  {passed:<6}  {_yes(case.passes_all):<6}  "
            f"{_yes(case.passes_some)}"
        )
    lines += [
        "",
        f"pass^{k}: {score.passes_all} of {total} cases, "
        f"pass@{k}: {score.passes_some} of {total} cases",
    ]

    problems: dict[str, list[str]] = {
        "Needs review": [],
        "Failed": [],
        "Errored": [],
        "Decided by a person": [],
    }
    for case_id, outcomes in outcomes_by_case.items():
        for attempt, outcome in enumerate(outcomes, start=1):
            problem = _problem(outcome)
            if problem is not None:
                section, detail = problem
                problems[section].append(f"  {case_id} #{attempt}: {detail}")
    for section, details in problems.items():
        if details:
            lines += ["", section, *details]
    return "\n".join(lines)


def _problem(outcome: Outcome) -> tuple[str, str] | None:
    if isinstance(outcome, Passed):
        return None
    elif isinstance(outcome, NeedsReview):
        return "Needs review", outcome.reason
    elif isinstance(outcome, Failed):
        return "Failed", outcome.reason
    elif isinstance(outcome, Errored):
        return "Errored", outcome.message
    elif isinstance(outcome, Reviewed):
        return "Decided by a person", f"{_verdict(outcome.passed)} ({outcome.note})"
    else:
        assert_never(outcome)


def _verdict(passed: bool) -> str:
    return "passed" if passed else "failed"


def _yes(value: bool) -> str:
    return "yes" if value else "no"
