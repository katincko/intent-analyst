"""Recording and replaying the exchange with the API, one file per question.

Built on vcrpy 9.2, which patches the transports of httpx2 (the HTTP library under the anthropic
SDK 2.3.0); its source was read for `httpx2` before choosing it over a transport of our own.

* Nothing secret reaches the file: only an allow-list of headers is kept, in the request and in the
  response, so the API key (`x-api-key`), any `authorization` header and cookies are dropped by
  default, and a file that still holds the key, or anything shaped like one, is deleted.
* A replay matches a request on method, URL and the parsed JSON body, so a changed prompt, tool or
  message finds no recording, and the replay raises instead of reaching a network.
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import vcr
from vcr.request import Request

# Headers that are safe to keep. Everything else, including every authentication header, is dropped.
KEPT_REQUEST_HEADERS = frozenset({"content-type", "anthropic-version"})
KEPT_RESPONSE_HEADERS = frozenset({"content-type", "request-id"})
_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{6,}")


class ReplayMismatch(Exception):
    """The loop sent a request the recording does not hold, or stopped before using all of it.

    The SDK turns the error of an unmatched request into `APIConnectionError`, which the loop
    reports as an `api_error` outcome, so the cause is read from the recording instead.
    """


class SecretInRecording(Exception):
    """A recording held an API key (or something shaped like one) and was deleted."""


def _scrub_request(request: Request) -> Request:
    request.headers = {
        k: v for k, v in request.headers.items() if k.lower() in KEPT_REQUEST_HEADERS
    }
    return request


def _scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    response["headers"] = {
        k: v for k, v in response["headers"].items() if k.lower() in KEPT_RESPONSE_HEADERS
    }
    return response


def _parsed(body: bytes | str | None) -> Any:
    return json.loads(body) if body else None


def _same_json_body(recorded: Request, sent: Request) -> None:
    if _parsed(recorded.body) != _parsed(sent.body):
        raise AssertionError("the request body is not the recorded one")  # how vcr matchers fail


def _vcr() -> vcr.VCR:
    machine = vcr.VCR(
        serializer="json",
        match_on=["method", "scheme", "host", "port", "path", "query", "json_body"],
        before_record_request=_scrub_request,
        before_record_response=_scrub_response,
        decode_compressed_response=True,
    )
    machine.register_matcher("json_body", _same_json_body)
    return machine


def assert_no_secret(path: Path, secret: str | None = None) -> None:
    """Delete the file and raise if it holds the key or anything shaped like an Anthropic key."""
    text = path.read_text(encoding="utf-8")
    if (secret and secret in text) or _KEY_SHAPE.search(text):
        path.unlink()
        raise SecretInRecording(f"{path.name} held an API key and was deleted")


@contextmanager
def recording(
    path: Path, mode: Literal["record", "replay"], secret: str | None = None
) -> Iterator[None]:
    """Record every exchange of the block into `path`, or replay it from there.

    A replay never sends anything. A request with no matching recording fails inside the SDK, and
    the block then raises `ReplayMismatch`, as does a recording that was not played to the end.
    """
    if mode == "replay":
        if not path.is_file():
            raise FileNotFoundError(f"no recording at {path}")
        with _vcr().use_cassette(str(path), record_mode="none") as cassette:
            yield
            if not cassette.all_played:
                played = len(cassette.play_counts)
                raise ReplayMismatch(
                    f"{path.name}: {played} of {len(cassette)} recorded exchanges were used; "
                    "the request the loop sends is not the recorded one"
                )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with _vcr().use_cassette(str(path), record_mode="all"):
        yield
    if path.exists():
        assert_no_secret(path, secret)
