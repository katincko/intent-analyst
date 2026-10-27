"""An in-memory stand-in for the Messages API, used instead of any network.

The responses have the shape of a real Message, read from the official docs:
* response object and usage fields: https://platform.claude.com/docs/en/api/messages/create
* tool_use blocks, tool_result blocks, is_error and the ordering rules:
  https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls
* error body (`{"type": "error", "error": {...}, "request_id": "req_..."}`) and the `request-id`
  response header: https://platform.claude.com/docs/en/api/errors
* thinking and redacted_thinking blocks, and the rule that they come back unmodified in the latest
  assistant message (`thinking` has `thinking` and `signature`, empty text when the display is
  omitted; `redacted_thinking` has `data`):
  https://platform.claude.com/docs/en/build-with-claude/thinking and the "Thinking blocks cannot
  be modified" section of the errors page. SDK types: `anthropic.types.ThinkingBlock`,
  `RedactedThinkingBlock`; the SDK reads the id from the header into `APIStatusError.request_id`.

The fake also enforces, like the real API, the rules of the handle-tool-calls page, so a loop that
drops a tool_result, splits results over several messages or puts text before them gets a 400
instead of passing silently. The same goes for a thinking block of the latest assistant message
that was edited, dropped or reordered. Nothing here opens a socket: it is an httpx2 MockTransport.
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


def thinking(signature: str, text_: str = "") -> dict[str, Any]:
    """A thinking block. The text is empty when the display is omitted (the default)."""
    return {"type": "thinking", "thinking": text_, "signature": signature}


def redacted_thinking(data: str) -> dict[str, Any]:
    return {"type": "redacted_thinking", "data": data}


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


def error_response(
    status: int, error_type: str, text_: str, request_id: str | None = None
) -> httpx2.Response:
    """An error as the API sends it: `request_id` in the body and in the `request-id` header."""
    request_id = request_id or f"req_{next(_ids):024d}"
    body = {
        "type": "error",
        "error": {"type": error_type, "message": text_},
        "request_id": request_id,
    }
    return httpx2.Response(status, json=body, headers={"request-id": request_id})


THINKING_TYPES = ("thinking", "redacted_thinking")
THINKING_MODIFIED = (
    "`thinking` or `redacted_thinking` blocks in the latest assistant message cannot be "
    "modified. These blocks must remain as they were in the original response."
)


def _same_thinking(original: dict[str, Any], sent: dict[str, Any]) -> bool:
    """A block must come back as received. The text of an omitted (empty) block is ignored."""
    if original["type"] != sent.get("type"):
        return False
    if original["type"] == "redacted_thinking":
        return bool(original["data"] == sent.get("data"))
    if original["signature"] != sent.get("signature"):
        return False
    return original["thinking"] == "" or bool(original["thinking"] == sent.get("thinking"))


def _rest_key(block: dict[str, Any]) -> tuple[str, str]:
    return (block["type"], str(block.get("id") or block.get("text") or ""))


def thinking_problem(body: dict[str, Any], issued: list[list[dict[str, Any]]]) -> str | None:
    """The 400 message when the latest assistant message changed the thinking of its reply."""
    messages = body["messages"]
    latest = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=None)
    if latest is None or not isinstance(messages[latest]["content"], list):
        return None
    sent = [b for b in messages[latest]["content"] if isinstance(b, dict)]
    rest = [_rest_key(b) for b in sent if b.get("type") not in THINKING_TYPES]
    got = [b for b in sent if b.get("type") in THINKING_TYPES]
    same_rest = [
        [b for b in reply if b["type"] in THINKING_TYPES]
        for reply in issued
        if [_rest_key(b) for b in reply if b["type"] not in THINKING_TYPES] == rest
    ]
    intact = any(
        len(want) == len(got) and all(_same_thinking(w, g) for w, g in zip(want, got, strict=True))
        for want in same_rest
    )
    if same_rest and not intact:  # no reply it could be: edited, dropped or reordered
        return f"messages.{latest}.content.0: {THINKING_MODIFIED}"
    return None


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
    issued: list[list[dict[str, Any]]] = field(default_factory=list)  # content of each reply sent
    timeouts: list[Any] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v1/messages", request.url
        body = json.loads(request.content)
        self.requests.append(body)
        self.timeouts.append(request.extensions.get("timeout"))
        if problem := contract_problem(body):
            return error_response(400, "invalid_request_error", problem)
        if problem := thinking_problem(body, self.issued):
            return error_response(400, "invalid_request_error", problem)
        assert self.script, "the loop made a request that the test did not script"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx2.Response):
            return item
        reply = item(body) if callable(item) else item
        self.clock.advance(reply.takes)
        self.issued.append(reply.body["content"])
        return httpx2.Response(200, json=reply.body)

    def client(self) -> anthropic.Anthropic:
        return anthropic.Anthropic(
            api_key="test-key-not-real",
            base_url="http://fake-messages-api.invalid",
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(self)),
        )
