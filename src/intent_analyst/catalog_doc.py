"""Renders CATALOG.md from the code, so the document cannot drift from the catalog."""

import json
from pathlib import Path
from typing import Any

from intent_analyst.catalog import INTENTS, Intent

_LIMITS = (
    ("minimum", "min"),
    ("maximum", "max"),
    ("maxLength", "max length"),
    ("pattern", "pattern"),
)


def _type(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    if "$ref" in prop:
        return _type(defs[prop["$ref"].rsplit("/", 1)[-1]], defs)
    if "anyOf" in prop:
        return " or ".join(_type(p, defs) for p in prop["anyOf"])
    if "enum" in prop:
        return "one of " + ", ".join(f"`{v}`" for v in prop["enum"])
    out = str(prop.get("type", "any"))
    for key, label in _LIMITS:
        if key in prop:
            out += f", {label} `{prop[key]}`"
    return out


def _render(intent: Intent) -> str:
    schema = intent.params.model_json_schema()
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    rows = [
        f"| `{name}` | {_type(prop, defs)} | "
        + ("required" if name in required else str(prop["default"]))
        + " |"
        for name, prop in schema["properties"].items()
    ]
    uses = "; ".join(
        f"`{t}`: " + ", ".join(f"`{c}`" for c in cols) for t, cols in intent.uses.items()
    )
    lines = [
        f"## `{intent.name}`",
        "",
        intent.summary,
        "",
        "| Parameter | Type and allowed values | Required or default |",
        "|---|---|---|",
        *rows,
        "",
        f"Reads: {uses}.",
        "",
        f'Example: "{intent.example_question}" with `{json.dumps(intent.example_params)}`.',
        "",
    ]
    return "\n".join(lines)


def render_catalog() -> str:
    head = (
        "# Intent catalog\n\n"
        "Generated from `src/intent_analyst/catalog.py` by "
        "`uv run python -m intent_analyst.catalog_doc`.\n"
        "A test fails if this file is stale. The model may only pick one of these intents.\n"
        "Sort and group columns are closed enums. No intent writes data.\n\n"
    )
    return head + "\n".join(_render(i) for i in INTENTS.values())


if __name__ == "__main__":
    Path("CATALOG.md").write_text(render_catalog(), encoding="utf-8", newline="\n")
