"""Gauntlet's data models: test cases, suites, run settings, outcomes and the web views."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FrozenModel(BaseModel):
    """Base for every model: can't be changed after creation, rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


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
    """Settings for one run: how many attempts per case, which database, which agent."""

    k: int = Field(ge=1, strict=True)  # strict: rejects True and 2.0
    db_path: Path
    agent: str = Field(min_length=1)


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


class Reviewed(FrozenModel):
    """A person decided an attempt that no check could."""

    kind: Literal["reviewed"] = "reviewed"
    passed: bool
    note: str = Field(min_length=1)


# Pydantic reads "kind" to pick the class when loading an outcome from JSON.
type Outcome = Annotated[
    Passed | Failed | Errored | NeedsReview | Reviewed, Field(discriminator="kind")
]


class Attempt(FrozenModel):
    """One attempt at a case: the agent's raw reply and how it was judged."""

    number: int = Field(ge=1)
    reply: str | None = None  # None when the agent errored
    outcome: Outcome


class Verdict(FrozenModel):
    """A person's decision on one attempt that needs review."""

    attempt: int = Field(ge=1)
    passed: bool
    note: str = Field(min_length=1)


class CaseScore(FrozenModel):
    """How one case did across its k attempts. A person's verdict counts like a check's."""

    case_id: str
    k: int
    passed: int
    failed: int
    errored: int
    needs_review: int
    reviewed: int  # how many of passed and failed a person decided
    passes_all: bool  # pass^k: every attempt passed
    passes_some: bool  # pass@k: at least one attempt passed


class SuiteScore(FrozenModel):
    """Every case's score, plus how many cases passed all and passed some attempts."""

    cases: tuple[CaseScore, ...]
    passes_all: int
    passes_some: int


class Options(FrozenModel):
    """What the start page can offer: the suites, the agents and which agent to tick first."""

    suites: tuple[str, ...]
    agents: tuple[str, ...]
    default_agent: str


class StartRequest(FrozenModel):
    """What the web page sends to start runs: one run per chosen agent."""

    suite: str = Field(min_length=1)
    k: int = Field(ge=1, le=20, strict=True)  # each attempt is a paid API call
    agents: tuple[str, ...] = Field(min_length=1)


class RunSummary(FrozenModel):
    """One line in the list of recent runs."""

    run_id: str
    status: str
    started: datetime


class CaseView(FrozenModel):
    """One case of a run as the web page shows it."""

    case: Case
    attempts: tuple[Attempt, ...] | None  # None while no worker is up to answer
    score: CaseScore | None  # None until all k attempts are in


class RunView(FrozenModel):
    """A run as the web page shows it: its settings, its status and every case so far."""

    run_id: str
    status: str
    suite: Suite
    config: RunConfig
    cases: tuple[CaseView, ...]
