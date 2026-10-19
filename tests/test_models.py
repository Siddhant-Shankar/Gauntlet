import pytest
from pydantic import ValidationError

from gauntlet.models import FrozenModel


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
