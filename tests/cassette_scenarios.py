"""Two scripted conversations used to make the committed recordings in tests/cassettes/.

The recordings were made through the in-memory fake (`fake_api.py`), not through the real API: they
exercise the recorder and the replay, and say nothing about how the model behaves. Remake them with
`uv run python tests/record_cassettes.py` after an intended change of the prompt, the tools or
these scripts. Ids are fixed so that the request bodies, which carry them, are the same every time.
"""

from dataclasses import dataclass
from pathlib import Path

from fake_api import Reply, message, tool_use, usage

CASSETTES = Path(__file__).resolve().parent / "cassettes"
BASE_URL = "http://fake-messages-api.invalid"
API_KEY = "test-key-not-real"


@dataclass(frozen=True)
class Scenario:
    name: str
    question: str
    status: str


def count_by_gender() -> list[Reply]:
    first = message(
        [
            tool_use(
                "run_intent",
                {"intent": "count_patients", "params": {"group_by": "gender"}},
                "toolu_cassette_0001",
            )
        ],
        token_usage=usage(60, 80, cache_creation_input_tokens=1100),
    )
    second = message(
        [
            tool_use(
                "answer",
                {
                    "text": "The table lists the number of patients for each gender.",
                    "sources": ["r1"],
                },
                "toolu_cassette_0002",
            )
        ],
        token_usage=usage(120, 60, cache_read_input_tokens=1100),
    )
    return [Reply(first), Reply(second)]


def refuse_a_write() -> list[Reply]:
    body = message(
        [tool_use("refuse", {"reason": "The catalog cannot change data."}, "toolu_cassette_0003")],
        token_usage=usage(50, 30, cache_read_input_tokens=1100),
    )
    return [Reply(body)]


SCENARIOS = {
    "count-by-gender": (
        Scenario("count-by-gender", "How many patients are there of each gender?", "answered"),
        count_by_gender,
    ),
    "refuse-write": (
        Scenario("refuse-write", "Delete every patient who has died.", "refused"),
        refuse_a_write,
    ),
}
