"""The real agent under test: Claude, asked for one SQLite query per question."""

from anthropic import Anthropic
from anthropic.types import TextBlock

DEFAULT_MODEL = "claude-sonnet-5-5"
REQUEST_TIMEOUT_SECONDS = 30.0
INSTRUCTIONS = (
    "You answer questions about a SQLite database. "
    "Reply with exactly one SQLite query that answers the question, and nothing else."
)


class ClaudeAgent:
    """Answers each question with one Messages API call."""

    def __init__(self, client: Anthropic, model: str = DEFAULT_MODEL) -> None:
        self._client = client
        self._model = model

    def answer(self, prompt: str, schema: str) -> str:
        message = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=INSTRUCTIONS,
            messages=[{"role": "user", "content": f"Schema:\n{schema}\n\nQuestion: {prompt}"}],
        )
        return "".join(block.text for block in message.content if isinstance(block, TextBlock))
