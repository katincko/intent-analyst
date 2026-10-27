"""Grounding check: every number in an answer must come from a table the tools returned.

Rules, kept deliberately strict:
* A number is a digit run, optionally signed, with thousands commas, a decimal part and a
  trailing percent sign. It matches a cell when the values are equal (88.7 and 88.70 match).
* Text cells never supply numbers. When the answer quotes a text cell verbatim (a medication
  such as "albuterol 5 MG/ML", a year bucket "2020", a date), that quote is masked before the
  scan, so the digits inside it are not checked and cannot be borrowed by an unrelated claim.
* Every digit of the answer is checked. A number glued to a letter ("99k", "99x", "1e3", "2nd",
  "v2") or to another dotted number ("1.2.3") is rejected whole, because it cannot be compared
  with a cell. Only a verbatim quote of a text cell (masked, see above) may contain such a token.
* Numbers typed in the question may be repeated ("the 5 most common"), because the user
  supplied them.
* Numbers written as words ("three") are not checked. This is a known gap, listed in the README.
"""

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

# Not glued to a word character or a dot before it, so "1.2.3" and "v2" are not read as numbers.
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?%?(?![\w])")
# What the answer scan reads. First a plain number that stands alone; otherwise any word made of
# letters, digits, underscores and inner dots that has a digit in it (a glued number).
_WORD = r"(?:\w|\.(?=\w))"
_TOKEN = re.compile(rf"(?<![\w.])-?\d+(?:,\d{{3}})*(?:\.\d+)?%?(?!\w|\.\d)|{_WORD}*\d{_WORD}*")
Cell = Any


def _decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.rstrip("%").replace(",", ""))
    except InvalidOperation:
        return None


def numbers_in(text: str) -> list[Decimal]:
    return [d for t in _NUMBER.findall(text) if (d := _decimal(t)) is not None]


def _mask_quoted_cells(text: str, cells: Iterable[Cell]) -> str:
    """Blank out verbatim quotes of text cells, longest first."""
    quotes = sorted({str(c) for c in cells if not _is_number(c) and c is not None}, key=len)
    for quote in reversed(quotes):
        if quote:
            text = re.sub(rf"(?<![\w.]){re.escape(quote)}(?![\w])", " ", text)
    return text


def _is_number(cell: Cell) -> bool:
    return isinstance(cell, int | float | Decimal) and not isinstance(cell, bool)


def ungrounded_numbers(
    text: str, tables: Sequence[Sequence[Sequence[Cell]]], question: str
) -> list[str]:
    """The numbers of `text` that no cell of `tables` (rows of cells) and not the question has."""
    cells = [cell for table in tables for row in table for cell in row]
    allowed = {Decimal(str(c)) for c in cells if _is_number(c)}
    allowed.update(numbers_in(question))
    missing = []
    for match in _TOKEN.finditer(_mask_quoted_cells(text, cells)):
        token = match.group()
        if _NUMBER.fullmatch(token) is None:  # glued to a letter or a dot: cannot be a cell
            missing.append(token)
            continue
        value = _decimal(token)
        if value is not None and value not in allowed:
            missing.append(token)
    return missing
