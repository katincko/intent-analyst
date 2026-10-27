"""V5: every exchange with the API can be recorded without a secret, and a replay of the recording
runs the loop with no network and goes red when the request changes (prompt, tools or messages).

The committed recordings in tests/cassettes/ were made through the in-memory fake, see
`cassette_scenarios.py`. The replay client below is a plain SDK client with the default HTTP
transport: nothing in these tests is a mock of the transport, and `conftest.py` refuses sockets.
"""

import gzip
import io
import json
from pathlib import Path
from typing import Any

import anthropic
import duckdb
import httpx2
import pytest
from cassette_scenarios import API_KEY, BASE_URL, CASSETTES, SCENARIOS
from fake_api import FakeMessagesApi, Reply, message, tool_use

from intent_analyst import agent, evaluate
from intent_analyst.agent import Outcome, ask
from intent_analyst.recording import ReplayMismatch, SecretInRecording, recording
from intent_analyst.tools import tool_definitions

MODEL = "claude-sonnet-5-5"
SECRET = "sk-ant-api03-FAKE0123456789abcdefghijklmnopqrstuvwx"  # noqa: S105 (not a real key)
TOKEN = "oauth-token-FAKE-9876543210zyxwvutsrqponm"  # noqa: S105 (not a real token)


def replay_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=API_KEY, base_url=BASE_URL)


def replay(
    con: duckdb.DuckDBPyConnection, name: str, question: str | None = None, model: str = MODEL
) -> Outcome:
    scenario, _ = SCENARIOS[name]
    with recording(CASSETTES / f"{name}.json", "replay"):
        return ask(question or scenario.question, client=replay_client(), con=con, model=model)


# ---- the replay ----


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_loop_runs_against_a_recording_with_no_network(
    con: duckdb.DuckDBPyConnection, name: str
) -> None:
    outcome = replay(con, name)
    assert outcome.status == SCENARIOS[name][0].status
    assert outcome.cost_usd > 0


def test_the_replay_gives_back_the_recorded_usage(con: duckdb.DuckDBPyConnection) -> None:
    outcome = replay(con, "count-by-gender")
    assert outcome.steps == 2
    assert (outcome.cache_write_tokens, outcome.cache_read_tokens) == (1100, 1100)
    assert outcome.tables and outcome.tables[0].columns == ["bucket", "patients"]


def test_a_changed_system_prompt_finds_no_recording(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = [{**agent.SYSTEM_BLOCKS[0], "text": agent.SYSTEM_PROMPT + "One more rule.\n"}]
    monkeypatch.setattr(agent, "SYSTEM_BLOCKS", changed)
    with pytest.raises(ReplayMismatch, match="not the recorded one"):
        replay(con, "count-by-gender")


def test_a_changed_tool_finds_no_recording(
    con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tool_definitions

    def changed() -> Any:
        tools = real()
        return [{**tools[0], "description": tools[0]["description"] + " Be quick."}, *tools[1:]]

    monkeypatch.setattr(agent, "tool_definitions", changed)
    with pytest.raises(ReplayMismatch):
        replay(con, "refuse-write")


def test_a_changed_message_finds_no_recording(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ReplayMismatch):
        replay(con, "refuse-write", question="Delete every patient who has died!")


def test_a_changed_model_finds_no_recording(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ReplayMismatch):
        replay(con, "refuse-write", model="claude-haiku-5-5")


def test_a_missing_recording_is_an_error_not_a_request(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError), recording(tmp_path / "none.json", "replay"):
        pytest.fail("the block must not run")


# ---- the recording holds no secret ----


def record(
    path: Path, con: duckdb.DuckDBPyConnection, script: list[Any], client_key: dict[str, Any]
) -> FakeMessagesApi:
    api = FakeMessagesApi(script)
    client = anthropic.Anthropic(
        base_url=BASE_URL,
        http_client=anthropic.DefaultHttpxClient(transport=_transport(api)),
        **client_key,
    )
    with recording(
        path, "record", secret=client_key.get("api_key") or client_key.get("auth_token")
    ):
        ask("Q?", client=client, con=con, model=MODEL, clock=api.clock)
    return api


def _transport(api: FakeMessagesApi) -> Any:
    return httpx2.MockTransport(api)


def refusing() -> list[Reply]:
    return [Reply(message([tool_use("refuse", {"reason": "No."}, "toolu_secret_0001")]))]


def test_the_api_key_is_sent_but_never_written(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "key.json"
    api = record(path, con, refusing(), {"api_key": SECRET})
    # anthropic 2.3.0 sends the key as a bearer token; the check is not vacuous
    assert api.headers[0]["authorization"] == f"Bearer {SECRET}"
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "authorization" not in text.lower() and "x-api-key" not in text.lower()
    assert json.loads(text)["interactions"]


def test_a_bearer_token_is_sent_but_never_written(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "token.json"
    api = record(path, con, refusing(), {"auth_token": TOKEN})
    assert api.headers[0]["authorization"] == f"Bearer {TOKEN}"
    text = path.read_text(encoding="utf-8")
    assert TOKEN not in text and "authorization" not in text.lower()


def test_only_listed_headers_are_kept(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    path = tmp_path / "headers.json"
    record(path, con, refusing(), {"api_key": SECRET})
    (interaction,) = json.loads(path.read_text(encoding="utf-8"))["interactions"]
    assert set(interaction["request"]["headers"]) <= {"content-type", "anthropic-version"}
    assert set(interaction["response"]["headers"]) <= {"content-type", "request-id"}


def test_a_response_that_echoes_the_key_deletes_the_recording(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "echo.json"
    echo = [
        Reply(message([tool_use("refuse", {"reason": f"bad key {SECRET}"}, "toolu_secret_0002")]))
    ]
    with pytest.raises(SecretInRecording):
        record(path, con, echo, {"api_key": SECRET})
    assert not path.exists()


def test_anything_shaped_like_a_key_deletes_the_recording(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    path = tmp_path / "shape.json"
    echo = [Reply(message([tool_use("refuse", {"reason": f"saw {SECRET}"}, "toolu_secret_0003")]))]
    api = FakeMessagesApi(echo)
    with pytest.raises(SecretInRecording), recording(path, "record"):  # recorder knows no key
        ask("Q?", client=api.client("other-key"), con=con, model=MODEL, clock=api.clock)
    assert not path.exists()


def test_a_compressed_response_is_recorded_decoded_and_replays(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    body = json.dumps(refusing()[0].body).encode()
    squeezed = httpx2.Response(
        200,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        stream=httpx2.ByteStream(gzip.compress(body)),
    )
    path = tmp_path / "gzip.json"
    record(path, con, [squeezed], {"api_key": SECRET})
    recorded = json.loads(path.read_text(encoding="utf-8"))["interactions"][0]["response"]
    assert "content-encoding" not in recorded["headers"]
    assert json.loads(recorded["body"]["string"])["content"][0]["name"] == "refuse"
    with recording(path, "replay"):
        outcome = ask("Q?", client=replay_client(), con=con, model=MODEL)
    assert outcome.status == "refused"


# ---- the executor records and replays ----


def fake_anthropic(monkeypatch: pytest.MonkeyPatch, replies: int) -> FakeMessagesApi:
    """Make `anthropic.Anthropic(...)` in the executor return a client over the in-memory fake."""
    api = FakeMessagesApi(
        [
            Reply(message([tool_use("refuse", {"reason": "No."}, f"toolu_cli_{i:04d}")]))
            for i in range(replies)
        ]
    )

    client = api.client(SECRET)  # built before the name is replaced

    def factory(*, api_key: str, **kwargs: Any) -> anthropic.Anthropic:
        return client

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    return api


def run_cli(args: list[str], environ: dict[str, str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    return evaluate.main(args, environ, out, err), out.getvalue(), err.getvalue()


def test_a_real_run_records_one_file_per_question_without_the_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_anthropic(monkeypatch, 3)
    code, out, err = run_cli(
        ["--yes", "--count", "3", "--record-dir", str(tmp_path)], {evaluate.KEY_VARIABLE: SECRET}
    )
    assert (code, err) == (0, "")
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert files == [
        "count_patients-01.json",
        "patients_by_age_band-01.json",
        "top_conditions-01.json",
    ]
    assert all(SECRET not in p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "questions run: 3" in out


def test_a_recorded_run_replays_with_no_key_and_the_same_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_anthropic(monkeypatch, 3)
    environ = {evaluate.KEY_VARIABLE: SECRET}
    _, first, _ = run_cli(["--yes", "--count", "3", "--record-dir", str(tmp_path)], environ)
    code, second, err = run_cli(["--replay", str(tmp_path)], {})
    assert (code, err) == (0, "")
    assert (
        first.split("counted spend")[1].split("\n")[0]
        == second.split("counted spend")[1].split("\n")[0]
    )
    assert "questions run: 3" in second


def test_a_replay_after_a_prompt_change_stops_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_anthropic(monkeypatch, 2)
    run_cli(
        ["--yes", "--count", "2", "--record-dir", str(tmp_path)], {evaluate.KEY_VARIABLE: SECRET}
    )
    changed = [{**agent.SYSTEM_BLOCKS[0], "text": agent.SYSTEM_PROMPT + "Extra.\n"}]
    monkeypatch.setattr(agent, "SYSTEM_BLOCKS", changed)
    code, _, err = run_cli(["--replay", str(tmp_path)], {})
    assert code == 1
    assert "not the recorded one" in err


def test_a_replay_of_an_empty_directory_is_refused(tmp_path: Path) -> None:
    code, _, err = run_cli(["--replay", str(tmp_path)], {})
    assert code == 2 and "no recording" in err
