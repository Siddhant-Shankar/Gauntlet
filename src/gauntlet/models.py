"""Gauntlet's data models: test cases, suites and run settings."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    """Base for every model: can't be changed after creation, rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SqlResult(FrozenModel):
    """The answer key for a case: a known-correct SQL query."""

    kind: Literal["sql_result"] = "sql_result"  # lets a second check kind be added later
    reference_sql: str = Field(min_length=1)


class Case(FrozenModel):
    """One question for the agent. The agent only ever sees `prompt`."""

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
