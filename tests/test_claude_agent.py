import json

import httpx2
import pytest
from anthropic import DEFAULT_MAX_RETRIES, Anthropic, APIStatusError

from gauntlet.claude_agent import INSTRUCTIONS, REQUEST_TIMEOUT_SECONDS, ClaudeAgent
from gauntlet.workflows import AGENT_TIME_LIMIT


class FakeApi:
    """Stands in for the Messages API: records each request body and sends back one response."""

    def __init__(self, response: httpx2.Response) -> None:
        self.bodies: list[dict[str, object]] = []
        self._response = response

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return self._response

    def client(self) -> Anthropic:
        transport = httpx2.MockTransport(self)
        return Anthropic(
            api_key="not-a-real-key", max_retries=0, http_client=httpx2.Client(transport=transport)
        )


def message(text: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    )


def test_the_agent_sends_the_question_and_schema() -> None:
    api = FakeApi(message("SELECT 1"))
    agent = ClaudeAgent(api.client(), model="claude-test")
    assert agent.answer("How many?", "CREATE TABLE t (x)") == "SELECT 1"
    [body] = api.bodies
    assert body["model"] == "claude-test"
    assert body["system"] == INSTRUCTIONS
    assert body["messages"] == [
        {"role": "user", "content": "Schema:\nCREATE TABLE t (x)\n\nQuestion: How many?"}
    ]


def test_the_reply_comes_back_raw() -> None:
    reply = "```sql\nSELECT 1\n```"
    agent = ClaudeAgent(FakeApi(message(reply)).client())
    assert agent.answer("q", "schema") == reply


def test_an_api_error_is_raised() -> None:
    error = {"type": "error", "error": {"type": "api_error", "message": "overloaded"}}
    agent = ClaudeAgent(FakeApi(httpx2.Response(500, json=error)).client())
    with pytest.raises(APIStatusError):
        agent.answer("q", "schema")


def test_the_client_gives_up_before_the_workflow_does() -> None:
    worst_case = (1 + DEFAULT_MAX_RETRIES) * REQUEST_TIMEOUT_SECONDS
    assert worst_case < AGENT_TIME_LIMIT.total_seconds()
