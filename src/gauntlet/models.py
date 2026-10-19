"""Gauntlet's data models: test cases, suites, run settings and outcomes."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    """Base for every model: can't be changed after creation, rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SqlResult(FrozenModel):
    """The answer key for a case: a known-correct SQL query."""

    kind: Literal["sql_result"] = "sql_result"  # lets a second check kind be added later
    reference_sql: str = Field(min_length=1)
    ordered: bool = False  # set when the question asks for an order, e.g. "top 3 by sales"


class Case(FrozenModel):
    """One question for the agent. The agent never sees `check`."""

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    check: SqlResult

    @field_validator("id")
    @classmethod
    def _shaped_like_a_name(cls, value: str) -> str:
        if not value.isidentifier():
            raise ValueError("case id must look like a name, e.g. count_customers")
        return value


class Suite(FrozenModel):
    """A named set of cases, loaded from a suite file."""

    name: str = Field(min_length=1)
    cases: tuple[Case, ...] = Field(min_length=1)  # a tuple, so it can't be appended to

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, cases: tuple[Case, ...]) -> tuple[Case, ...]:
        ids = [case.id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return cases


class RunConfig(FrozenModel):
    """Settings for one run: how many attempts per case, and which database."""

    k: int = Field(ge=1)
    db_path: Path


class Passed(FrozenModel):
    """The agent's rows matched the reference rows."""

    kind: Literal["passed"] = "passed"


class Failed(FrozenModel):
    """The agent answered, and the answer was wrong."""

    kind: Literal["failed"] = "failed"
    reason: str = Field(min_length=1)


class Errored(FrozenModel):
    """No answer came back: the agent crashed or timed out."""

    kind: Literal["errored"] = "errored"
    message: str = Field(min_length=1)


class NeedsReview(FrozenModel):
    """No check could decide, so a person has to look."""

    kind: Literal["needs_review"] = "needs_review"
    reason: str = Field(min_length=1)


# Pydantic reads "kind" to pick the class when loading an outcome from JSON.
type Outcome = Annotated[Passed | Failed | Errored | NeedsReview, Field(discriminator="kind")]
