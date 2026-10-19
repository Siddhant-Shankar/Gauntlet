"""Gauntlet's data: the test cases and how to run them.

Every model here crosses a boundary (suite files on disk, Temporal payloads),
so all of them inherit FrozenModel.
"""

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """A model that can't change after creation and rejects unknown fields.

    Frozen so no code can alter a test case or a verdict mid-run; extra="forbid"
    so a misplaced or misspelled field in input fails loudly instead of being
    silently ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
