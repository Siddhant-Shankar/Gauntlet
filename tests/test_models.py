import json
from pathlib import Path
from typing import Any, assert_never

import pytest
from pydantic import TypeAdapter, ValidationError

from gauntlet.models import (
    Attempt,
    Case,
    Errored,
    Failed,
    FrozenModel,
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


# --- FrozenModel ---


class Sample(FrozenModel):
    name: str


def test_subclass_is_frozen() -> None:
    sample = Sample(name="a")
    with pytest.raises(ValidationError) as exc:
        sample.name = "b"  # type: ignore[misc]  # assigning on purpose to prove it's frozen
    assert first_error_type(exc) == "frozen_instance"


def test_subclass_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        Sample.model_validate({"name": "a", "extra": 1})
    assert first_error_type(exc) == "extra_forbidden"


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


def test_case_survives_a_json_round_trip() -> None:
    case = Case.model_validate(valid_case())
    assert Case.model_validate_json(case.model_dump_json()) == case


def test_check_kind_is_filled_in_and_written_to_json() -> None:
    check = SqlResult(reference_sql="SELECT 1")
    assert check.kind == "sql_result"
    assert json.loads(check.model_dump_json())["kind"] == "sql_result"


def test_row_order_is_ignored_by_default() -> None:
    assert SqlResult(reference_sql="SELECT 1").ordered is False


def test_row_order_can_be_required_in_a_suite_file() -> None:
    data = valid_case()
    data["check"]["ordered"] = True
    assert Case.model_validate(data).check.ordered is True


def test_unknown_check_kind_is_rejected() -> None:
    data = valid_case()
    data["check"]["kind"] = "regex"
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "literal_error"


@pytest.mark.parametrize("field", ["id", "prompt"])
def test_empty_text_fields_are_rejected(field: str) -> None:
    data = valid_case()
    data[field] = ""
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "string_too_short"


def test_empty_reference_sql_is_rejected() -> None:
    data = valid_case()
    data["check"]["reference_sql"] = ""
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "string_too_short"


@pytest.mark.parametrize("blank", ["   ", "\t\n"])
def test_blank_text_is_rejected(blank: str) -> None:
    data = valid_case()
    data["prompt"] = blank
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "string_too_short"
    data = valid_case()
    data["check"]["reference_sql"] = blank
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "string_too_short"


def test_surrounding_whitespace_is_trimmed() -> None:
    data = valid_case()
    data["id"] = "  count_customers  "
    assert Case.model_validate(data).id == "count_customers"


@pytest.mark.parametrize(
    "bad_id",
    ["count customers", "count\tcustomers", "count-customers", "2024_sales", "q1/count"],
)
def test_id_not_shaped_like_a_name_is_rejected(bad_id: str) -> None:
    data = valid_case()
    data["id"] = bad_id
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "value_error"


def test_unknown_top_level_field_is_rejected() -> None:
    data = valid_case()
    data["k"] = 5
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "extra_forbidden"


def test_unknown_field_inside_check_is_rejected() -> None:
    data = valid_case()
    data["check"]["tolerance"] = 0.01
    with pytest.raises(ValidationError) as exc:
        Case.model_validate(data)
    assert first_error_type(exc) == "extra_forbidden"


def test_case_is_frozen() -> None:
    case = Case.model_validate(valid_case())
    with pytest.raises(ValidationError) as exc:
        case.prompt = "changed"  # type: ignore[misc]  # assigning on purpose to prove it's frozen
    assert first_error_type(exc) == "frozen_instance"


# --- Suite ---


def valid_suite() -> dict[str, Any]:
    return {"name": "demo_sql", "cases": [valid_case("count_customers"), valid_case("count_again")]}


def test_suite_loads_from_json_with_cases_as_a_tuple() -> None:
    suite = Suite.model_validate_json(json.dumps(valid_suite()))
    assert suite.name == "demo_sql"
    assert isinstance(suite.cases, tuple)
    assert [case.id for case in suite.cases] == ["count_customers", "count_again"]


def test_suite_survives_a_json_round_trip() -> None:
    suite = Suite.model_validate(valid_suite())
    assert Suite.model_validate_json(suite.model_dump_json()) == suite


def test_suite_with_no_cases_is_rejected() -> None:
    data = valid_suite()
    data["cases"] = []
    with pytest.raises(ValidationError) as exc:
        Suite.model_validate(data)
    assert first_error_type(exc) == "too_short"


def test_suite_with_empty_name_is_rejected() -> None:
    data = valid_suite()
    data["name"] = ""
    with pytest.raises(ValidationError) as exc:
        Suite.model_validate(data)
    assert first_error_type(exc) == "string_too_short"


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


def test_run_config_loads() -> None:
    config = RunConfig.model_validate(VALID_CONFIG)
    assert config.k == 5
    assert config.db_path == Path("data/demo.db")
    assert config.agent == "claude-sonnet-5-5"


def test_run_config_survives_a_json_round_trip() -> None:
    config = RunConfig(k=5, db_path=Path("data/demo.db"), agent="claude-sonnet-5-5")
    assert RunConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("bad_k", [0, -1])
def test_k_below_one_is_rejected(bad_k: int) -> None:
    with pytest.raises(ValidationError) as exc:
        RunConfig.model_validate({**VALID_CONFIG, "k": bad_k})
    assert first_error_type(exc) == "greater_than_equal"


@pytest.mark.parametrize("missing", ["k", "db_path", "agent"])
def test_run_config_fields_are_required(missing: str) -> None:
    data: dict[str, Any] = dict(VALID_CONFIG)
    del data[missing]
    with pytest.raises(ValidationError) as exc:
        RunConfig.model_validate(data)
    assert first_error_type(exc) == "missing"


@pytest.mark.parametrize("bad_k", [True, 2.0, "5"])
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
    ("raw", "expected_type"),
    [
        ('{"kind": "passed"}', Passed),
        ('{"kind": "failed", "reason": "rows differ"}', Failed),
        ('{"kind": "errored", "message": "timeout"}', Errored),
        ('{"kind": "needs_review", "reason": "rows differ"}', NeedsReview),
        ('{"kind": "reviewed", "passed": false, "note": "wrong table"}', Reviewed),
    ],
)
def test_kind_label_picks_the_class(raw: str, expected_type: type[object]) -> None:
    assert type(outcomes.validate_json(raw)) is expected_type


@pytest.mark.parametrize(
    ("raw", "error_type"),
    [
        ('{"kind": "maybe", "reason": "x"}', "union_tag_invalid"),
        ('{"reason": "rows differ"}', "union_tag_not_found"),
        ('{"kind": "passed", "reason": "rows differ"}', "extra_forbidden"),
        ('{"kind": "failed"}', "missing"),
        ('{"kind": "failed", "reason": ""}', "string_too_short"),
        ('{"kind": "errored", "message": ""}', "string_too_short"),
        ('{"kind": "reviewed", "passed": true, "note": " "}', "string_too_short"),
        ('{"kind": "reviewed", "note": "ok"}', "missing"),
    ],
)
def test_bad_outcome_json_is_rejected(raw: str, error_type: str) -> None:
    with pytest.raises(ValidationError) as exc:
        outcomes.validate_json(raw)
    assert first_error_type(exc) == error_type


def test_wrong_kind_in_code_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        Failed(kind="passed", reason="x")  # type: ignore[arg-type]  # wrong on purpose
    assert first_error_type(exc) == "literal_error"


def test_every_kind_is_handled() -> None:
    assert [describe(outcome) for outcome in ALL_KINDS] == [
        "pass",
        "fail: expected [(59,)], got [(58,)]",
        "error: timeout after 30s",
        "review: no SQL in answer",
        "reviewed: passed (2 is right)",
    ]


# --- Attempt and Verdict ---


def test_attempt_survives_a_json_round_trip() -> None:
    attempt = Attempt(number=2, reply="SELECT 1", outcome=Failed(reason="rows differ"))
    assert Attempt.model_validate_json(attempt.model_dump_json()) == attempt


def test_an_errored_attempt_has_no_reply() -> None:
    assert Attempt(number=1, outcome=Errored(message="timeout")).reply is None


def test_attempt_numbers_start_at_one() -> None:
    with pytest.raises(ValidationError) as exc:
        Attempt(number=0, outcome=Passed())
    assert first_error_type(exc) == "greater_than_equal"


def test_a_verdict_needs_a_note() -> None:
    with pytest.raises(ValidationError) as exc:
        Verdict(attempt=1, passed=True, note="  ")
    assert first_error_type(exc) == "string_too_short"
