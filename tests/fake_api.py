"""An in-memory stand-in for the Messages API, used instead of any network.

The responses have the shape of a real Message, read from the official docs:
* response object and usage fields: https://platform.claude.com/docs/en/api/messages/create
* tool_use blocks, tool_result blocks, is_error and the ordering rules:
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
* error body (`{"type": "error", "error": {...}}`): https://platform.claude.com/docs/en/api/errors

The fake also enforces, like the real API, the rules of the handle-tool-calls page, so a loop that
drops a tool_result, splits results over several messages or puts text before them gets a 400
instead of passing silently. Nothing here opens a socket: it is an httpx2 MockTransport.
"""

import itertools
import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2

MODEL = "claude-sonnet-5-5"
_ids = itertools.count(1)


class FakeClock:
    """A monotonic clock that only moves when told to."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def usage(input_tokens: int = 100, output_tokens: int = 50, **extra: int) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "service_tier": "standard",
        **extra,
    }


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(name: str, tool_input: dict[str, Any], id: str | None = None) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": id or f"toolu_{next(_ids):024d}",
        "name": name,
        "input": tool_input,
    }


def message(
    content: list[dict[str, Any]],
    stop_reason: str | None = None,
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Message response body. Default stop_reason: tool_use if it has a tool_use block."""
    if stop_reason is None:
        has_tool = any(b["type"] == "tool_use" for b in content)
        stop_reason = "tool_use" if has_tool else "end_turn"
    return {
        "id": f"msg_{next(_ids):024d}",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": token_usage or usage(),
    }


@dataclass
class Reply:
    """A scripted response. `takes` seconds pass on the fake clock while the API "works"."""

    body: dict[str, Any]
    takes: float = 0.0


def error_response(status: int, error_type: str, text_: str) -> httpx2.Response:
    body = {"type": "error", "error": {"type": error_type, "message": text_}}
    return httpx2.Response(status, json=body)


def contract_problem(body: dict[str, Any]) -> str | None:
    """What the real API would reject in the tool-use turns of a request, or None."""
    messages = body["messages"]
    if not messages or messages[0]["role"] != "user":
        return "first message must use the user role"
    for i, msg in enumerate(messages):
        content = msg["content"] if isinstance(msg["content"], list) else []
        if msg["role"] == "assistant":
            used = [b["id"] for b in content if b.get("type") == "tool_use"]
            if not used:
                continue
            following = messages[i + 1] if i + 1 < len(messages) else None
            if following is None or following["role"] != "user":
                return "tool_use ids were found without tool_result blocks immediately after"
            answered = [
                str(b.get("tool_use_id"))
                for b in following["content"]
                if isinstance(b, dict) and b.get("type") == "tool_result"
            ]
            if sorted(answered) != sorted(used):
                return (
                    f"tool_use ids {sorted(used)} must each have one tool_result "
                    f"in the next message, found {sorted(answered)}"
                )
        else:
            kinds = [b.get("type") for b in content if isinstance(b, dict)]
            if "tool_result" in kinds:
                seen_other = False
                for kind in kinds:
                    if kind != "tool_result":
                        seen_other = True
                    elif seen_other:
                        return "tool_result blocks must come first in the content array"
                before = messages[i - 1] if i > 0 else None
                if before is None or before["role"] != "assistant":
                    return "tool_result without a preceding assistant tool_use"
    return None


@dataclass
class FakeMessagesApi:
    """Plays a script of replies and records every request it receives."""

    script: list[Any]  # Reply | Exception | httpx2.Response | Callable[[dict], Reply]
    clock: FakeClock = field(default_factory=FakeClock)
    requests: list[dict[str, Any]] = field(default_factory=list)
    timeouts: list[Any] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages", request.url
        body = json.loads(request.content)
        self.requests.append(body)
        self.timeouts.append(request.extensions.get("timeout"))
        if problem := contract_problem(body):
            return error_response(400, "invalid_request_error", problem)
        assert self.script, "the loop made a request that the test did not script"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx2.Response):
            return item
        reply = item(body) if callable(item) else item
        self.clock.advance(reply.takes)
        return httpx2.Response(200, json=reply.body)

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key-not-real",
            base_url="http://fake-messages-api.invalid",
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self)),
        )
