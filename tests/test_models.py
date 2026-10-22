import json
from typing import Any, assert_never

import pytest
from pydantic import TypeAdapter, ValidationError

from gauntlet.models import (
    Case,
    Errored,
    Failed,
    NeedsReview,
    Outcome,
    Passed,
    Reviewed,
    RunConfig,
    SqlResult,
    Suite,
    Verdict,
)


def first_error_type(exc: pytest.ExceptionInfo[ValidationError]) -> str:
    return str(exc.value.errors()[0]["type"])


# --- Case and SqlResult ---


def valid_case(case_id: str = "count_customers") -> dict[str, Any]:
    return {
        "id": case_id,
        "prompt": "How many customers are there?",
        "check": {"kind": "sql_result", "reference_sql": "SELECT COUNT(*) FROM customers"},
    }


def test_case_loads_from_json() -> None:
    case = Case.model_validate_json(json.dumps(valid_case()))
    assert case.id == "count_customers"
    assert case.prompt == "How many customers are there?"
    assert case.check == SqlResult(reference_sql="SELECT COUNT(*) FROM customers")


def test_case_is_frozen() -> None:
    case = Case.model_validate(valid_case())
    with pytest.raises(ValidationError) as exc:
        case.prompt = "changed"  # type: ignore[misc]  # assigning on purpose to prove it's frozen
    assert first_error_type(exc) == "frozen_instance"


def test_unknown_check_kind_is_rejected() -> None:
    data = valid_case()
    data["check"]["kind"] = "regex"
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "literal_error"


def test_blank_text_is_rejected() -> None:
    data = valid_case()
    data["prompt"] = "   "
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "string_too_short"


def test_surrounding_whitespace_is_trimmed() -> None:
    data = valid_case()
    data["id"] = "  count_customers  "
    assert Case.model_validate(data).id == "count_customers"


@pytest.mark.parametrize("bad_id", ["count customers", "2024_sales"])
def test_id_not_shaped_like_a_name_is_rejected(bad_id: str) -> None:
    data = valid_case()
    data["id"] = bad_id
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "value_error"


# --- Suite ---


def valid_suite() -> dict[str, Any]:
    return {"name": "demo_sql", "cases": [valid_case("count_customers"), valid_case("count_again")]}


def test_suite_loads_from_json_with_cases_as_a_tuple() -> None:
    suite = Suite.model_validate_json(json.dumps(valid_suite()))
    assert suite.name == "demo_sql"
    assert isinstance(suite.cases, tuple)
    assert [case.id for case in suite.cases] == ["count_customers", "count_again"]


def test_suite_with_no_cases_is_rejected() -> None:
    data = valid_suite()
    data["cases"] = []
    with pytest.raises(ValidationError) as exc:
        Suite.model_validate(data)
    assert first_error_type(exc) == "too_short"


def test_suite_with_duplicate_case_ids_is_rejected() -> None:
    data = valid_suite()
    data["cases"] = [valid_case("count_customers"), valid_case("count_customers")]
    with pytest.raises(ValidationError) as exc:
        Suite.model_validate(data)
    assert first_error_type(exc) == "value_error"
    assert "case ids must be unique" in str(exc.value)


def test_k_in_a_suite_file_is_rejected() -> None:
    # k belongs to RunConfig; putting it in the suite file must fail, not be ignored.
    data = valid_suite()
    data["k"] = 5
    with pytest.raises(ValidationError) as exc:
        Suite.model_validate(data)
    assert first_error_type(exc) == "extra_forbidden"


# --- RunConfig ---


VALID_CONFIG = {"k": 5, "db_path": "data/demo.db", "agent": "claude-sonnet-5-5"}


def test_k_below_one_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        RunConfig.model_validate({**VALID_CONFIG, "k": 0})
    assert first_error_type(exc) == "greater_than_equal"


@pytest.mark.parametrize("bad_k", [True, 2.0])
def test_k_must_be_a_whole_number(bad_k: object) -> None:
    with pytest.raises(ValidationError) as exc:
        RunConfig.model_validate({**VALID_CONFIG, "k": bad_k})
    assert first_error_type(exc) == "int_type"


# --- Outcome ---

outcomes: TypeAdapter[Outcome] = TypeAdapter(Outcome)

ALL_KINDS: list[Outcome] = [
    Passed(),
    Failed(reason="expected [(59,)], got [(58,)]"),
    Errored(message="timeout after 30s"),
    NeedsReview(reason="no SQL in answer"),
    Reviewed(passed=True, note="2 is right"),
]


def describe(outcome: Outcome) -> str:
    # mypy fails this file if a kind is missing from the if chain.
    if isinstance(outcome, Passed):
        return "pass"
    elif isinstance(outcome, Failed):
        return f"fail: {outcome.reason}"
    elif isinstance(outcome, Errored):
        return f"error: {outcome.message}"
    elif isinstance(outcome, NeedsReview):
        return f"review: {outcome.reason}"
    elif isinstance(outcome, Reviewed):
        return f"reviewed: {'passed' if outcome.passed else 'failed'} ({outcome.note})"
    else:
        assert_never(outcome)


@pytest.mark.parametrize("outcome", ALL_KINDS)
def test_outcome_survives_a_json_round_trip(outcome: Outcome) -> None:
    loaded = outcomes.validate_json(outcomes.dump_json(outcome))
    assert loaded == outcome
    assert type(loaded) is type(outcome)


def test_kind_is_filled_in_and_written_to_json() -> None:
    failed = Failed(reason="rows differ")
    assert failed.kind == "failed"
    assert json.loads(failed.model_dump_json()) == {"kind": "failed", "reason": "rows differ"}


@pytest.mark.parametrize(
    ("raw", "error_type"),
    [
        ('{"kind": "maybe", "reason": "x"}', "union_tag_invalid"),
        ('{"kind": "failed"}', "missing"),
    ],
)
def test_bad_outcome_json_is_rejected(raw: str, error_type: str) -> None:
    with pytest.raises(ValidationError) as exc:
        outcomes.validate_json(raw)
    assert first_error_type(exc) == error_type


def test_every_kind_is_handled() -> None:
    assert [describe(outcome) for outcome in ALL_KINDS] == [
        "pass",
        "fail: expected [(59,)], got [(58,)]",
        "error: timeout after 30s",
        "review: no SQL in answer",
        "reviewed: passed (2 is right)",
    ]


# --- Verdict ---


def test_a_verdict_needs_a_note() -> None:
    with pytest.raises(ValidationError) as exc:
        Verdict(attempt=1, passed=True, note="  ")
    assert first_error_type(exc) == "string_too_short"
