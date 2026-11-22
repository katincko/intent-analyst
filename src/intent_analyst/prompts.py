"""The versioned system prompts. A prompt is a text file in `prompt_versions/` and is never edited
once a run has used it: a change is a new file, a new version and a new recording.

* `v1` is the prompt of the paid run of 2026-10-28, kept byte for byte. Its 92 recordings hold it
  in every request, and a replay matches the request body, so changing one byte of `v1.txt` makes
  that replay fail (`tests/test_prompt_versions.py`).
* The latest version is the default of every new run. Every report says which version ran.

The tool definitions are the same for every version (a changed one would break the replay too).
"""

from dataclasses import dataclass
from pathlib import Path

from anthropic.types import TextBlockParam

_DIRECTORY = Path(__file__).resolve().parent / "prompt_versions"


@dataclass(frozen=True)
class PromptVersion:
    name: str
    text: str
    # The loop rules that came with the text (agent.py): the loose match of a quoted cell, the hint
    # on a refused answer, the check for leaked markup and the stop on a repeated refusal. The
    # first version has them off because its recordings hold the replies of the loop of
    # 2026-10-28, and a replay matches the request body, so a changed reply would not replay.
    # ponytail: retire the switch with the v1 recordings; revisit when v1 is no longer replayed.
    guided: bool


def _read(name: str, guided: bool) -> PromptVersion:
    return PromptVersion(name, (_DIRECTORY / f"{name}.txt").read_bytes().decode("utf-8"), guided)


PROMPTS: dict[str, PromptVersion] = {
    v.name: v for v in (_read("v1", guided=False), _read("v2", guided=True))
}
# A report written before the versions existed (the run of 2026-10-28) was made by the first one.
LEGACY_NAME = "v1"
LATEST_NAME = max(PROMPTS, key=lambda name: int(name[1:]))


def get(name: str | None) -> PromptVersion:
    """The version called `name`, or the latest one for None."""
    return PROMPTS[name or LATEST_NAME]


# Prompt caching (https://platform.claude.com/docs/en/build-with-claude/prompt-caching): the cache
# covers the prompt in the order tools, system, messages, up to the block with `cache_control`. One
# breakpoint on the system block therefore caches both the tool definitions and the system prompt,
# the part that is the same for every question. The 1 hour lifetime matches the cache write price
# in pricing.py. A prefix under the model minimum (512 tokens for the 5.5 models) is silently not
# cached, so the evaluation report shows the tokens read from the cache for every question.
def system_blocks(version: PromptVersion) -> list[TextBlockParam]:
    return [
        {
            "type": "text",
            "text": version.text,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        }
    ]
