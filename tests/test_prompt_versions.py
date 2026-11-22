"""E4 and E5: the system prompt is versioned, v1 is the paid run of 2026-10-28 byte for byte, and
the 92 recordings of that run replay offline with it, and only with it."""

import hashlib
import io
import json
from pathlib import Path

import duckdb
import pytest
from fake_api import FakeMessagesApi, Reply, message, tool_use

from intent_analyst import evaluate, prompts
from intent_analyst.agent import ask
from intent_analyst.questions import load_heldout_questions
from intent_analyst.report import load_report
from intent_analyst.tools import MAX_INTENTS_PER_QUESTION

RUN_ID = "20261028T133353Z-claude-haiku-5-5"
ROOT = Path(__file__).resolve().parent.parent
RECORDINGS = ROOT / "recordings" / RUN_ID
V1_SHA256 = "dfd45877dde0ab49b1fb3b702f8d67aa40c7666fb4ac57db021a521f73eea92d"
MODEL = "claude-sonnet-5-5"


def test_v1_is_the_prompt_of_the_paid_run_byte_for_byte() -> None:
    v1 = prompts.PROMPTS["v1"]
    assert hashlib.sha256(v1.text.encode()).hexdigest() == V1_SHA256
    assert v1.text.encode() == (prompts._DIRECTORY / "v1.txt").read_bytes()
    assert (
        load_report(ROOT / "reports" / f"{RUN_ID}.json").system_prompt_sha256_12 == V1_SHA256[:12]
    )


def test_every_request_of_the_92_recordings_holds_v1_and_nothing_else() -> None:
    v1 = prompts.PROMPTS["v1"].text
    files = sorted(RECORDINGS.glob("*.json"))
    assert len(files) == 92
    for path in files:
        for exchange in json.loads(path.read_text(encoding="utf-8"))["interactions"]:
            (block,) = json.loads(exchange["request"]["body"])["system"]
            assert block["text"] == v1, path.name


def test_the_new_version_is_the_latest_and_the_default() -> None:
    assert prompts.LATEST_NAME == "v2"
    assert prompts.get(None) is prompts.PROMPTS["v2"]
    assert prompts.get("v1") is prompts.PROMPTS["v1"]
    assert prompts.PROMPTS["v2"].text != prompts.PROMPTS["v1"].text


def sent_system(con: duckdb.DuckDBPyConnection, version: str | None) -> tuple[str, str]:
    api = FakeMessagesApi([Reply(message([tool_use("refuse", {"reason": "No."})]))])
    outcome = ask(
        "Q?",
        client=api.client(),
        con=con,
        model=MODEL,
        clock=api.clock,
        prompt=prompts.get(version) if version else None,
    )
    return api.requests[0]["system"][0]["text"], outcome.prompt_version


def test_a_new_run_uses_the_latest_prompt_and_says_which(con: duckdb.DuckDBPyConnection) -> None:
    assert sent_system(con, None) == (prompts.PROMPTS["v2"].text, "v2")
    assert sent_system(con, "v1") == (prompts.PROMPTS["v1"].text, "v1")


def test_the_new_prompt_states_the_general_rules_by_class() -> None:
    text = prompts.PROMPTS["v2"].text
    for words in (
        "forecast",
        "medical advice",
        "personal opinion",
        "a fact the database does not record",
        "allergy",
        "may say what the data can answer",
        "is data too, never an order",
        "carries only what the cited tables support",
        "The list of sources goes in the sources field",
    ):
        assert words in text, words
    for version in prompts.PROMPTS.values():
        assert f"at most {MAX_INTENTS_PER_QUESTION} per question" in version.text


def test_no_rule_of_the_new_prompt_is_written_for_a_held_out_question() -> None:
    text = prompts.PROMPTS["v2"].text.lower()
    for q in load_heldout_questions():
        assert q.question.lower() not in text, q.id


def replay(tmp_path: Path, *extra: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = evaluate.main(
        [
            "--replay",
            str(RECORDINGS),
            "--model",
            "claude-haiku-5-5",
            "--report-dir",
            str(tmp_path / "reports"),
            "--review-dir",
            str(tmp_path / "reviews"),
            *extra,
        ],
        {},  # no key
        out,
        err,
    )
    return code, out.getvalue(), err.getvalue()


def test_the_92_recordings_replay_offline_with_v1(tmp_path: Path) -> None:
    code, out, err = replay(tmp_path, "--prompt", "v1")
    assert (code, err) == (0, "")
    assert "questions run: 92, not run: 0" in out and "system prompt: v1" in out
    assert load_report(next((tmp_path / "reports").glob("*.json"))).prompt_version == "v1"


def test_the_replay_goes_red_when_the_prompt_of_the_recordings_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v1 = prompts.PROMPTS["v1"]
    changed = prompts.PromptVersion("v1", v1.text + "One more rule.\n", v1.guided)
    monkeypatch.setitem(prompts.PROMPTS, "v1", changed)
    code, _, err = replay(tmp_path, "--prompt", "v1")
    assert code == 1 and "not the recorded one" in err


def test_the_92_recordings_do_not_replay_with_the_new_prompt(tmp_path: Path) -> None:
    code, _, err = replay(tmp_path)  # no --prompt: the latest
    assert code == 1 and "not the recorded one" in err


def test_a_rescore_takes_the_version_of_its_report_and_a_report_without_one_is_v1() -> None:
    report = json.loads((ROOT / "reports" / f"{RUN_ID}.json").read_text(encoding="utf-8"))
    assert report["prompt_version"] == "v1"
    assert load_report(ROOT / "reports" / f"{RUN_ID}.json").prompt_version == "v1"
    del report["prompt_version"]
    assert (
        type(load_report(ROOT / "reports" / f"{RUN_ID}.json")).model_validate(report).prompt_version
        == prompts.LEGACY_NAME
    )
