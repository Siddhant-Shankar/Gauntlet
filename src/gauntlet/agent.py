"""What an agent is given and what it gives back, plus a fake agent for tests."""

from pathlib import Path
from typing import Protocol

from gauntlet.sql_check import connect_read_only

FENCE = "```"


class Agent(Protocol):
    """Anything with this method can be evaluated: a question and the schema in, a reply out."""

    def answer(self, prompt: str, schema: str) -> str: ...


class FakeAgent:
    """Replies from a script, so tests can make an agent pass, fail or crash on purpose."""

    def __init__(self, script: dict[str, list[str | Exception]]) -> None:
        # Copied, so answering doesn't empty the caller's lists.
        self._script = {prompt: list(replies) for prompt, replies in script.items()}

    def answer(self, prompt: str, schema: str) -> str:
        replies = self._script.get(prompt)
        if not replies:
            raise LookupError(f"fake agent has no reply left for {prompt!r}")
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def read_schema(db_path: Path) -> str:
    """Return the database's CREATE TABLE statements, so the agent knows the column names."""
    conn = connect_read_only(db_path)
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    # Names starting with sqlite_ are tables SQLite makes for itself.
    return "\n\n".join(sql for name, sql in rows if not name.startswith("sqlite_"))


def extract_sql(reply: str) -> str:
    """Return the SQL inside the reply's first ``` block, or the whole reply if it has none."""
    before, fence, after = reply.partition(FENCE)
    if not fence:
        return reply.strip()
    first_line, newline, rest = after.partition("\n")
    # The first line is the language tag, e.g. "sql", unless the whole block is one line.
    body = rest if newline else first_line
    sql, _, _ = body.partition(FENCE)
    return sql.strip() or before.strip()  # a lone closing fence: the SQL came before it
