"""The static page of an evaluation run: one HTML file, no script, no external resource.

    uv run python -m intent_analyst.page

reads the committed report (`reports/<run id>.json`), the human reading of the injection questions
(`reviews/<run id>.json`) and the recordings (`recordings/<run id>/`), and writes `docs/index.html`.
Every number on the page is a field of the report. Every step of a question is a tool call the model
made, read from its recording, with the result the next request carried back. The SQL is not in the
recordings: it is rebuilt here by the project's assembler from the recorded parameters, and the page
says so. All text that comes from the model or the recordings is escaped. The same inputs give the
same bytes.

The design is the approved direction A: one page, one accordion (`details`) per question, "What
went wrong" first, then every question by block. The page follows the system light or dark theme
and fits a 375 px phone; a wide table scrolls inside its own box.
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

from intent_analyst.catalog import assemble
from intent_analyst.questions import QUESTIONS_PATH, load_questions
from intent_analyst.report import (
    BlockSummary,
    QuestionRecord,
    Report,
    final_state,
    load_report,
)
from intent_analyst.review import ReviewFile, load_reviews

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "docs" / "index.html"
TERMINAL_TOOLS = ("answer", "refuse", "ask_clarification")
TABLE_ROWS = 4  # rows of a returned table shown on the page
SQL_LEGEND = (
    "SQL the catalog builds from these parameters "
    "(rebuilt for this page, not part of the recording)"
)  # ---------- recordings ----------


@dataclass(frozen=True)
class Step:
    """One tool call the model made, or one reply with no tool call (`tool` is empty)."""

    tool: str
    input: dict[str, Any]
    result: str | None  # None: no later request carries it (the last reply of the question)
    is_error: bool
    last_turn: bool  # the call came in the last reply of the question
    text: str = ""  # a reply with no tool call: what it said


def _json_body(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and "string" in body:
        body = body["string"]
    parsed = json.loads(body) if isinstance(body, str) else body
    return parsed if isinstance(parsed, dict) else {}


def _results_in(request: dict[str, Any]) -> dict[str, tuple[str, bool]]:
    """The tool results the request carries back, by tool_use id."""
    messages = request.get("messages") or []
    content = messages[-1].get("content") if messages else None
    found: dict[str, tuple[str, bool]] = {}
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            raw = block.get("content")
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            found[str(block.get("tool_use_id"))] = (text, bool(block.get("is_error")))
    return found


def load_steps(path: Path) -> list[Step]:
    """Every tool call of one recording, in order, with the result the next request carried."""
    exchanges = json.loads(path.read_text(encoding="utf-8"))["interactions"]
    requests = [_json_body(e["request"]["body"]) for e in exchanges]
    responses = [_json_body(e["response"]["body"]) for e in exchanges]
    steps: list[Step] = []
    for i, response in enumerate(responses):
        last = i == len(responses) - 1
        results = {} if last else _results_in(requests[i + 1])
        blocks = response.get("content") or []
        calls = [b for b in blocks if b.get("type") == "tool_use"]
        if not calls:
            said = " ".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
            steps.append(Step("", {}, None, False, last, said.strip()))
        for call in calls:
            result, is_error = results.get(str(call.get("id")), (None, False))
            steps.append(
                Step(str(call["name"]), dict(call.get("input") or {}), result, is_error, last)
            )
    return steps


# ---------- small formatters ----------


# Written as code points: the repository forbids these two characters in any text file, and the
# model's text holds them. They go out as character references, so the browser shows the same.
_DASH_REFERENCES = {chr(0x2014): "&#8212;", chr(0x2013): "&#8211;"}


def esc(value: object) -> str:
    text = escape(str(value), quote=True)
    for dash, reference in _DASH_REFERENCES.items():
        text = text.replace(dash, reference)
    return text


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def usd(value: Decimal) -> str:
    return f"${value:.4f}"


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ":"))


def cell(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _natural(question_id: str) -> tuple[str, int]:
    name, _, number = question_id.rpartition("-")
    return name, int(number) if number.isdigit() else 0


# ---------- theme ----------

LIGHT = {
    "bg": "#f6f7f9",
    "card": "#ffffff",
    "sunk": "#fbfbfc",
    "ink": "#14171f",
    "ink2": "#3d4350",
    "ink3": "#5b6370",
    "line": "#d9dde3",
    "line2": "#eef0f3",
    "accent": "#1f6f5c",
    "accent-soft": "#e6f2ee",
    "error": "#b00020",
    "error-soft": "#fdecee",
    "warn": "#7a4f00",
    "warn-soft": "#fff4db",
    "mark": "#ffe08a",
    "mark-ink": "#14171f",
}
DARK = {
    "bg": "#0f1217",
    "card": "#171b22",
    "sunk": "#12161c",
    "ink": "#e8eaee",
    "ink2": "#c2c7d0",
    "ink3": "#9aa3b0",
    "line": "#2c323c",
    "line2": "#232830",
    "accent": "#5fc4a8",
    "accent-soft": "#14302a",
    "error": "#ff8a98",
    "error-soft": "#3a1820",
    "warn": "#f0c060",
    "warn-soft": "#3a2c0e",
    "mark": "#ffe08a",
    "mark-ink": "#14171f",
}
# (text token, background token): every text on the page sits on one of these, at 4.5:1 or more
CONTRAST_PAIRS = (
    ("ink", "bg"),
    ("ink", "card"),
    ("ink", "sunk"),
    ("ink2", "bg"),
    ("ink2", "card"),
    ("ink3", "bg"),
    ("ink3", "card"),
    ("ink3", "sunk"),
    ("accent", "accent-soft"),
    ("accent", "card"),
    ("error", "error-soft"),
    ("error", "card"),
    ("warn", "warn-soft"),
    ("mark-ink", "mark"),
)


def _vars(tokens: dict[str, str]) -> str:
    return "".join(f"--{k}:{v};" for k, v in tokens.items())


CSS = (
    ":root{"
    + _vars(LIGHT)
    + "color-scheme:light dark;"
    + "--font:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;"
    + "--mono:ui-monospace,'Cascadia Mono',Consolas,monospace}\n"
    + "@media (prefers-color-scheme:dark){:root{"
    + _vars(DARK)
    + "}}\n"
    + """
*{box-sizing:border-box}
body{margin:0;background:var(--bg)}
.page{font-family:var(--font);color:var(--ink);background:var(--bg);font-size:14px}
.mono{font-family:var(--mono)}
.top{padding:40px 56px 0}
.top h1{margin:0 0 6px;font-size:26px;letter-spacing:-0.015em}
.top .sub{color:var(--ink3);font-size:14px;line-height:1.5;max-width:760px;margin:0}
.banner{margin-top:14px;display:inline-flex;gap:8px;align-items:center;padding:6px 10px;\
border-radius:8px;background:var(--accent-soft);color:var(--accent);font-size:12.5px;\
font-weight:600}
.kpis{display:flex;gap:12px;padding:22px 56px 0}
.kpi{flex:1;background:var(--card);border:1px solid var(--line);border-radius:12px;\
padding:14px 16px}
.kpi b{display:block;font-size:22px;letter-spacing:-0.01em}
.kpi span{color:var(--ink3);font-size:12.5px}
.sec{padding:26px 56px 0}
.sec h2{font-size:15px;margin:0 0 10px}
.sec h3{font-size:13px;margin:18px 0 8px;color:var(--ink2);font-family:var(--mono);\
font-weight:600}
.tbl{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);\
font-size:13px}
.tbl th{text-align:left;font-weight:600;color:var(--ink3);font-size:12px;padding:9px 12px;\
border-bottom:1px solid var(--line);background:var(--sunk)}
.tbl th.num,.num{text-align:right;font-variant-numeric:tabular-nums}
.tbl td{padding:8px 12px;border-bottom:1px solid var(--line2)}
.chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;\
font-size:11.5px;font-weight:650;white-space:nowrap}
.chip.ok{background:var(--accent-soft);color:var(--accent)}
.chip.bad{background:var(--error-soft);color:var(--error)}
.chip.warn{background:var(--warn-soft);color:var(--warn)}
.q{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:8px}
.qh{display:grid;grid-template-columns:18px 1fr auto;gap:12px;align-items:center;\
padding:11px 14px;cursor:pointer;list-style:none}
.qh::-webkit-details-marker{display:none}
.chev svg{display:block}
.chev .dn{display:none}
details[open]>.qh .chev .dn{display:block}
details[open]>.qh .chev .rt{display:none}
.qh .t{font-size:13.5px;overflow-wrap:anywhere}
.qh .id{color:var(--ink3);font-size:11.5px;font-family:var(--mono);display:block;margin-top:2px}
.row2{display:flex;gap:12px;align-items:center}
.qh .m{color:var(--ink3);font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
.loop{border-top:1px solid var(--line);padding:14px 16px 16px 44px}
.st{position:relative;padding:0 0 14px 18px;border-left:2px solid var(--line)}
.st:last-child{padding-bottom:0}
.st::before{content:'';position:absolute;left:-6px;top:3px;width:10px;height:10px;\
border-radius:50%;background:var(--card);border:2px solid var(--accent)}
.st.err::before{border-color:var(--error)}
.st.skip::before{border-color:var(--ink3);border-style:dashed}
.st h4{margin:0 0 6px;font-size:13px;font-weight:650;display:flex;flex-wrap:wrap;\
gap:4px 8px;align-items:baseline}
.st h4 code{font-family:var(--mono);font-size:12px;font-weight:500;color:var(--ink3);\
overflow-wrap:anywhere}
.st h4 .tag{font-family:var(--font);font-size:11.5px;font-weight:600;color:var(--error)}
.st h4 .tag.g{color:var(--ink3)}
.cap{font-size:11.5px;color:var(--ink3);margin-top:6px}
pre{margin:4px 0 0;font-family:var(--mono);font-size:12px;line-height:1.5;\
background:var(--sunk);border:1px solid var(--line);border-radius:8px;padding:10px 12px;\
white-space:pre-wrap;overflow-wrap:anywhere;color:var(--ink)}
.scroll{overflow-x:auto;max-width:100%}
.mini{border-collapse:collapse;font-size:12px;margin-top:6px;background:var(--card)}
.mini th,.mini td{border:1px solid var(--line);padding:4px 8px;text-align:left}
.mini th{background:var(--sunk);color:var(--ink3);font-weight:600}
.rej{margin-top:6px;padding:8px 10px;border-radius:8px;background:var(--error-soft);\
color:var(--error);font-size:12.5px;overflow-wrap:anywhere}
.ans{margin-top:6px;padding:10px 12px;border-radius:8px;background:var(--sunk);\
border:1px solid var(--line);color:var(--ink);font-size:13px;line-height:1.5;\
white-space:pre-wrap;overflow-wrap:anywhere}
.ans.ok{background:var(--accent-soft);border-color:transparent}
mark{background:var(--mark);color:var(--mark-ink);padding:0 2px;border-radius:3px;\
font-weight:650}
.stop{padding:8px 10px;border-radius:8px;background:var(--warn-soft);color:var(--warn);\
font-size:12.5px}
.flags{margin:0 0 14px;padding:10px 12px;border-radius:8px;background:var(--warn-soft);\
color:var(--warn);font-size:12.5px;line-height:1.5;overflow-wrap:anywhere}
.flags b{color:var(--ink)}
.foot{padding:28px 56px 40px;color:var(--ink3);font-size:12px;overflow-wrap:anywhere}
@media (max-width:640px){
.top{padding:24px 16px 0}
.top h1{font-size:21px}
.kpis{padding:16px 16px 0;flex-wrap:wrap;gap:8px}
.kpi{flex:1 1 calc(50% - 4px);padding:10px 12px}
.kpi b{font-size:18px}
.sec{padding:18px 16px 0}
.qh{grid-template-columns:14px 1fr}
.row2{grid-column:2;flex-direction:row-reverse;justify-content:flex-end}
.loop{padding:12px 12px 14px 20px}
.foot{padding:18px 16px 24px}
}
"""
)

# ---------- icons ----------

_PATHS = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "alert": '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/>'
    '<line x1="12" x2="12.01" y1="16" y2="16"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 '
    "1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 "
    '1 1z"/><path d="m9 12 2 2 4-4"/>',
    "chev": '<path d="m9 18 6-6-6-6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
    '<path d="M12 17h.01"/>',
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>'
    '<line x1="4" x2="4" y1="22" y2="15"/>',
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
}


def icon(name: str, size: int = 14, cls: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    return (
        f'<svg{klass} width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{_PATHS[name]}</svg>'
    )


# ---------- blocks of the page ----------

STOP_LABELS = {"max_steps": "step limit", "tool_errors": "tool errors", "timeout": "time limit"}


def chip(q: QuestionRecord) -> str:
    def make(kind: str, name: str, label: str) -> str:
        return f'<span class="chip {kind}">{icon(name, 11)}{esc(label)}</span>'

    if q.block == "injection":
        state = final_state(q)
        if state == "obeyed":
            return make("bad", "x", "obeyed")
        if state == "resisted":
            return make("ok", "shield", "resisted")
        return make("warn", "alert", "awaiting review")
    if q.status == "stopped":
        label = STOP_LABELS.get(q.stop_reason or "", (q.stop_reason or "stopped").replace("_", " "))
        return make("warn", "alert", label)
    if q.correct and q.status == "refused":
        return make("ok", "ban", "refused")
    if q.correct and q.status == "clarification":
        return make("ok", "help", "asked back")
    if q.correct:
        return make("ok", "check", "correct")
    if q.block == "out_of_scope" and q.status == "answered":
        return make("bad", "x", "answered, should refuse")
    return make("bad", "x", "wrong")


def went_wrong(records: list[QuestionRecord]) -> list[QuestionRecord]:
    """Stopped questions and the wrong ones, in run order. An injection question that no person
    has read yet is not wrong."""
    return [
        q
        for q in records
        if q.status == "stopped" or (not q.correct and final_state(q) != "review")
    ]


def highlight(text: str, marks: list[str]) -> str:
    """The text escaped, with every occurrence of a forbidden text wrapped in <mark>."""
    found = [m for m in marks if m]
    if not found:
        return esc(text)
    pattern = re.compile("|".join(re.escape(m) for m in sorted(found, key=len, reverse=True)), re.I)
    out: list[str] = []
    at = 0
    for hit in pattern.finditer(text):
        out.append(esc(text[at : hit.start()]))
        out.append(f"<mark>{esc(hit.group(0))}</mark>")
        at = hit.end()
    out.append(esc(text[at:]))
    return "".join(out)


def table_html(result: str) -> str:
    try:
        data = json.loads(result)
    except ValueError:
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("columns"), list):
        return ""
    columns, rows = data["columns"], data.get("rows") or []
    head = "".join(f"<th>{esc(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{esc(cell(v))}</td>" for v in row) + "</tr>"
        for row in rows[:TABLE_ROWS]
    )
    if len(rows) > TABLE_ROWS:
        rest = len(rows) - TABLE_ROWS
        body += f'<tr><td colspan="{len(columns)}">{rest} more rows in the result</td></tr>'
    return (
        f'<div class="scroll"><table class="mini"><tr>{head}</tr>{body}</table></div>'
        f'<div class="cap">result {esc(data.get("result_id", ""))}</div>'
    )


def sql_html(step: Step) -> str:
    """The SQL the assembler builds from the recorded parameters, and the values it binds."""
    params = step.input.get("params")
    try:
        query = assemble(str(step.input.get("intent")), params if isinstance(params, dict) else {})
    except (KeyError, ValueError, TypeError):
        return ""  # parameters the catalog refuses build no query
    bound = ", ".join(f"${k} = {v}" for k, v in sorted(query.params.items()))
    return f'<div class="cap">{esc(SQL_LEGEND)}</div><pre>{esc(query.sql)}</pre>' + (
        f'<div class="cap">bound: {esc(bound)}</div>' if bound else ""
    )


def _why(result: str) -> str:
    if result.startswith("Numbers not found"):
        return "refused by the grounding check"
    if result.startswith(("Invalid input", "Invalid params")):
        return "refused: the tool input was malformed"
    return "refused: the tool returned an error"


def flags_html(q: QuestionRecord) -> str:
    if q.block != "injection" or not q.flags:
        return ""
    said = {
        "forbidden_text": "the forbidden text appears in",
        "protected_text": "the system prompt appears in",
        "forbidden_call": "a call that does what the injection asks:",
    }
    parts = "; ".join(f"{said[f.kind]} {esc(f.where)}" for f in q.flags)
    verdict = q.human_verdict
    read = f"A person read it: {esc(verdict)}." if verdict else "No person has read it yet."
    quoted = (
        " The model quoted the text only to refuse it."
        if (verdict == "resisted" and q.status == "refused")
        else ""
    )
    return (
        f'<div class="flags">{icon("flag", 12)} <b>Flagged by the checks</b>: {parts}. '
        f"<b>{read}</b>{quoted}</div>"
    )


def loop_html(q: QuestionRecord, steps: list[Step], marks: list[str]) -> str:
    stopped = q.status == "stopped"
    terminal_errors: dict[tuple[str, str], int] = {}
    for s in steps:
        if s.tool in TERMINAL_TOOLS and s.is_error and s.result is not None:
            key = (s.tool, s.result)
            terminal_errors[key] = terminal_errors.get(key, 0) + 1
    seen: set[tuple[str, str]] = set()
    repeated: set[tuple[str, str]] = set()
    good = q.human_verdict == "resisted" if q.block == "injection" else q.correct
    out: list[str] = []
    stop_shown = False
    for step in steps:
        tool = esc(step.tool)
        if not step.tool:
            said = f'<div class="ans">{esc(step.text)}</div>' if step.text else ""
            out.append(
                '<div class="st err"><h4>no tool call '
                '<span class="tag">the reply had no tool call</span></h4>' + said + "</div>"
            )
        elif step.tool == "list_intents":
            try:
                listed = json.loads(step.result or "")
                count = f" of {len(listed)} intents" if isinstance(listed, list) else ""
            except ValueError:
                count = ""
            out.append(
                f'<div class="st"><h4>list_intents <span class="tag g">read the catalog{count}'
                "</span></h4></div>"
            )
        elif step.tool in TERMINAL_TOOLS:
            text = str(
                step.input.get("text")
                or step.input.get("reason")
                or step.input.get("question")
                or ""
            )
            if step.result is None and stopped:
                out.append(
                    f'<div class="st skip"><h4>{tool} <span class="tag g">no result recorded'
                    f'</span></h4><div class="stop">{esc(q.message)}</div></div>'
                )
                stop_shown = True
            elif step.is_error and step.result is not None:
                key = (step.tool, step.result)
                if key in seen:
                    if key not in repeated:
                        repeated.add(key)
                        more = terminal_errors[key] - 1
                        times = "again" if more == 1 else f"{more} more times"
                        out.append(
                            f'<div class="st err"><h4>{tool} <span class="tag">the same call '
                            f"refused {times}</span></h4></div>"
                        )
                else:
                    seen.add(key)
                    out.append(
                        f'<div class="st err"><h4>{tool} <span class="tag">'
                        f"{esc(_why(step.result))}</span></h4>"
                        f'<div class="ans">{highlight(text, marks)}</div>'
                        f'<div class="rej">{esc(step.result)}</div></div>'
                    )
            else:
                out.append(
                    f'<div class="st{"" if good else " err"}"><h4>{tool} '
                    f'<span class="tag g">final</span></h4>'
                    f'<div class="ans{" ok" if good else ""}">{highlight(text, marks)}</div></div>'
                )
        else:
            parts = [f"<h4>{tool} <code>{esc(compact_json(step.input))}</code>"]
            if step.result is None:
                parts.append('<span class="tag g">no result recorded</span>')
            elif step.is_error:
                parts.append(f'<span class="tag">{esc(_why(step.result))}</span>')
            parts.append("</h4>")
            if step.tool == "run_intent":
                parts.append(sql_html(step))
            if step.result is not None and step.is_error:
                parts.append(f'<div class="rej">{esc(step.result)}</div>')
            elif step.result is not None:
                parts.append(table_html(step.result))
            klass = "st err" if step.is_error else "st"
            out.append(f'<div class="{klass}">{"".join(parts)}</div>')
    if stopped and not stop_shown:
        out.append(
            f'<div class="st skip"><h4>stopped</h4><div class="stop">{esc(q.message)}</div></div>'
        )
    return flags_html(q) + "".join(out)


def question_html(
    q: QuestionRecord, steps: list[Step], marks: list[str], open_now: bool = False
) -> str:
    noun = "step" if q.steps == 1 else "steps"
    return (
        f'<details class="q"{" open" if open_now else ""}><summary class="qh">'
        f'<span class="chev">{icon("chev", 14, "rt")}{icon("down", 14, "dn")}</span>'
        f'<div class="t">{esc(q.question)}<span class="id">{esc(q.id)}</span></div>'
        f'<div class="row2"><span class="m">{q.steps} {noun} · {usd(q.cost_usd)}</span>'
        f"{chip(q)}</div></summary>"
        f'<div class="loop">{loop_html(q, steps, marks)}</div></details>'
    )


def header_html(report: Report) -> str:
    return (
        '<div class="top">\n<h1>Intent analyst: evaluation run</h1>\n'
        f'<p class="sub">{report.questions_run} questions in English about synthetic patients, '
        f"asked once to {esc(report.model)} on {esc(report.date)}. Each question opens into what "
        "the model did, step by step: the tools it called with their parameters, the table each "
        "call returned, and what the checks refused.</p>\n"
        f'<div class="banner">{icon("play", 12)}Recorded runs, real model output</div>\n</div>'
    )


def kpis_html(report: Report) -> str:
    inj = next((b for b in report.blocks if b.block == "injection"), None)
    items = [
        (pct(report.correct_rate), "correct overall"),
        (pct(report.coverage), "of the data questions answered"),
    ]
    if inj is not None:
        waiting = f", {inj.review} still waiting for a person" if inj.review else ""
        items.append(
            (
                f"{inj.resisted or 0} of {inj.questions}",
                f"injections resisted, each read by a person{waiting}",
            )
        )
    items.append((usd(report.total_cost_usd), "for the whole run"))
    cards = "".join(
        f'<div class="kpi"><b>{esc(n)}</b><span>{esc(t)}</span></div>' for n, t in items
    )
    return f'<div class="kpis">{cards}</div>'


def blocks_html(blocks: list[BlockSummary]) -> str:
    rows = "".join(
        f'<tr><td class="mono">{esc(b.block)}</td><td class="num">{b.questions}</td>'
        f'<td class="num">{pct(b.correct_rate)}</td><td class="num">{usd(b.cost_usd)}</td>'
        f'<td class="num">{b.mean_seconds:.1f}</td></tr>'
        for b in blocks
    )
    return (
        '<div class="sec"><h2>Results by block</h2><div class="scroll">'
        '<table class="tbl"><tr><th>Block</th><th class="num">Questions</th>'
        '<th class="num">Correct</th><th class="num">Cost</th>'
        '<th class="num">Seconds per question</th></tr>' + rows + "</table></div></div>"
    )


def with_review(report: Report, reviews: ReviewFile) -> Report:
    """The report with the person's verdict on each injection question, read from the review file.

    The numbers of the report are kept as they are. A report that already holds a verdict that the
    review file does not, or the review of another run, is refused instead of picking one."""
    if reviews.run_id != report.run_id:
        raise ValueError(f"the review file is for run {reviews.run_id}, not {report.run_id}")
    verdicts = {item.id: item.verdict or None for item in reviews.items}
    records = []
    for r in report.questions:
        if r.block != "injection":
            records.append(r)
            continue
        verdict = verdicts.get(r.id)
        if r.human_verdict is not None and r.human_verdict != verdict:
            raise ValueError(f"{r.id}: the report says {r.human_verdict}, the review {verdict}")
        records.append(r.model_copy(update={"human_verdict": verdict}))
    return report.model_copy(update={"questions": records})


def build_page(
    report_path: Path, review_path: Path, recordings: Path, questions_path: Path = QUESTIONS_PATH
) -> str:
    """The whole page, from the report, the review file and the recordings of one run."""
    report = with_review(load_report(report_path), load_reviews(review_path))
    expected = load_questions(questions_path)
    forbidden = {q.id: q.expect.forbidden for q in expected}
    steps = {q.id: load_steps(recordings / f"{q.id}.json") for q in report.questions}

    def render(q: QuestionRecord, open_now: bool = False) -> str:
        return question_html(q, steps[q.id], forbidden.get(q.id, []), open_now)

    wrong = went_wrong(report.questions)
    opened = wrong[-1].id if wrong else None
    by_block: list[str] = []
    for b in report.blocks:
        items = sorted((q for q in report.questions if q.block == b.block), key=_id_key)
        by_block.append(
            f"<h3>{esc(b.block)} · {b.questions}</h3>" + "".join(render(q) for q in items)
        )
    body = "\n".join(
        [
            header_html(report),
            kpis_html(report),
            blocks_html(report.blocks),
            f'<div class="sec"><h2>What went wrong ({len(wrong)})</h2>'
            + "".join(render(q, q.id == opened) for q in wrong)
            + "</div>",
            '<div class="sec"><h2>Every question, by block</h2>' + "".join(by_block) + "</div>",
            f'<div class="foot">Built by a script from reports/{esc(report.run_id)}.json, '
            f"reviews/{esc(report.run_id)}.json and recordings/{esc(report.run_id)}/. "
            "Synthetic patients (Synthea). MIT.</div>",
        ]
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light dark">\n'
        "<title>Intent analyst: evaluation run</title>\n"
        f"<style>{CSS}</style>\n</head>\n"
        f'<body>\n<main class="page">\n{body}\n</main>\n</body>\n</html>\n'
    )


def _id_key(q: QuestionRecord) -> tuple[str, int]:
    return _natural(q.id)


def latest_run_id(reports: Path) -> str:
    runs = sorted(p.stem for p in reports.glob("*.json"))
    if not runs:
        raise FileNotFoundError(f"no report in {reports}")
    return runs[-1]


def write_page(out: Path, run_id: str | None = None, root: Path = ROOT) -> Path:
    run = run_id or latest_run_id(root / "reports")
    page = build_page(
        root / "reports" / f"{run}.json",
        root / "reviews" / f"{run}.json",
        root / "recordings" / run,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8", newline="\n")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m intent_analyst.page", description=__doc__)
    p.add_argument("--run-id", help="the run to render (default: the latest report)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}")
    args = p.parse_args(argv)
    try:
        path = write_page(args.out, args.run_id)
    except (OSError, ValueError, KeyError) as error:
        print(f"Page not written: {error}", file=sys.stderr)
        return 2
    print(f"page written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
