"""Gauntlet's data: the test cases and how to run them.

Every model here crosses a boundary (suite files on disk, Temporal payloads),
so all of them inherit FrozenModel.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    """A model that can't change after creation and rejects unknown fields.

    Frozen so no code can alter a test case or a verdict mid-run; extra="forbid"
    so a misplaced or misspelled field in input fails loudly instead of being
    silently ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class SqlResult(FrozenModel):
    """Answer key for a SQL question: a known-correct query to compare rows against."""

    # The tag is written into the JSON so a second check kind can later join a
    # tagged union. The default saves passing kind= when building one in code.
    kind: Literal["sql_result"] = "sql_result"
    reference_sql: str = Field(min_length=1)


class Case(FrozenModel):
    """One question. The agent only ever sees `prompt`, never `check`."""

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    check: SqlResult

    @field_validator("id")
    @classmethod
    def _shaped_like_a_name(cls, value: str) -> str:
        # Ids get typed into commands and printed in reports, so they follow
        # Python's identifier rule: letters, digits, underscores, no leading digit.
        if not value.isidentifier():
            raise ValueError("case id must look like a name, e.g. count_customers")
        return value
