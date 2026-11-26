"""Recognise a soil association code in a question.

A user may leave out the leading zero ("813" for "0813"), leave out the letter, or
misspell the word "association". The codes come from the KB-103 association records
in the database. An exact code is pinned into the answer's context. A code that fits
several associations, or none, gets a "Did you mean" reply instead of a guess.

**Created:** 25-11-2026 (UK style).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from . import store_pg

ASSOCIATION_PREFIX = "KB-103-A-"
MAX_CHOICES = 10
NEAR_CUTOFF = 0.75

# Three or four digits, an optional leading zero, and an optional letter. Digits that
# are part of a longer number, a decimal or a thousands separator are not codes.
_CODE = re.compile(r"(?<![\w.,])0?(\d{3,4})([A-Za-z]?)(?![\w])")
_NAME = re.compile(r"^(?P<name>.*?)\s*\(map unit", re.DOTALL)
_KEY = re.compile(r"^0*(\d+)([a-z]?)$")

_records_cache: list[tuple[str, str]] = []


@dataclass(frozen=True)
class CodeMatch:
    """``exact`` names one record. ``ambiguous`` and ``near`` carry choices to offer."""

    kind: str
    typed: str
    chunk_id: str = ""
    choices: tuple[tuple[str, str], ...] = ()


def _key(code: str) -> tuple[int, str] | None:
    match = _KEY.match(code.lower())
    return (int(match.group(1)), match.group(2)) if match else None


def _name(head: str) -> str:
    match = _NAME.match(head or "")
    name = (match.group("name") if match else "").strip()
    if not name:
        return ""
    return name if name.lower().endswith("association") else f"{name} association"


def association_records() -> list[tuple[str, str]]:
    """Return ``(code, name)`` for every association record. Cached once found."""
    global _records_cache
    if _records_cache:
        return _records_cache
    heads = store_pg.fetch_chunk_heads(ASSOCIATION_PREFIX)
    _records_cache = [
        (row["chunk_id"][len(ASSOCIATION_PREFIX):], _name(row["head"])) for row in heads
    ]
    return _records_cache


def mentions_association(question: str) -> bool:
    """True if a word looks like 'association' or 'map unit', typos included."""
    for word in re.findall(r"[a-z]+", (question or "").lower()):
        if len(word) < 4:
            continue
        if word.startswith("assoc") or word in {"unit", "mapunit", "code"}:
            return True
        if difflib.SequenceMatcher(None, word, "association").ratio() >= 0.8:
            return True
    return False


def _choices(records: list[tuple[str, str]], codes: list[str]) -> tuple[tuple[str, str], ...]:
    wanted = set(codes)
    return tuple(record for record in records if record[0] in wanted)[:MAX_CHOICES]


def match_association(question: str, records: list[tuple[str, str]]) -> CodeMatch | None:
    """Match the first code in the question against the known association codes."""
    found = _CODE.search(question or "")
    return _match_found(question, found, records) if found else None


def match_all_associations(question: str, records: list[tuple[str, str]]) -> list[CodeMatch]:
    """Match every code in the question, in the order they are written."""
    matches = []
    found_all = list(_CODE.finditer(question or ""))
    # A bare number counts as a code only when the question also has a lettered code,
    # for example "compare 813 with 1024c". On its own it could be a year.
    lenient = any(found.group(2) for found in found_all)
    for found in found_all:
        match = _match_found(question, found, records, lenient=lenient)
        if match:
            matches.append(match)
    return matches


def _match_found(
    question: str, found, records: list[tuple[str, str]], lenient: bool = False
) -> CodeMatch | None:
    number, letter = int(found.group(1)), found.group(2).lower()
    typed = found.group(0).strip()
    named = mentions_association(question)
    if not named and not letter and not lenient:
        return None  # A bare number such as a year is not a code.

    by_key = {key: record for record in records if (key := _key(record[0]))}
    if (number, letter) in by_key:
        code = by_key[(number, letter)][0]
        return CodeMatch("exact", typed, chunk_id=ASSOCIATION_PREFIX + code)

    siblings = [record[0] for record in records if (_key(record[0]) or (None,))[0] == number]
    if siblings:
        return CodeMatch("ambiguous", typed, choices=_choices(records, siblings))
    if not named:
        return None

    labels = {f"{key[0]}{key[1]}": record[0] for key, record in by_key.items()}
    close = difflib.get_close_matches(f"{number}{letter}", labels, n=3, cutoff=NEAR_CUTOFF)
    if close:
        return CodeMatch("near", typed, choices=_choices(records, [labels[c] for c in close]))
    return None


MAX_PINNED = 4


def find_associations(question: str) -> tuple[list[str], CodeMatch | None]:
    """Return the chunk IDs of every code named in the question.

    The second value is a match that needs a choice (ambiguous or near). When it is
    set, the chat asks the user to choose and no answer is drawn. A database problem
    never blocks the chat: it returns no codes.
    """
    try:
        records = association_records()
    except Exception:
        return [], None
    if not records:
        return [], None
    matches = match_all_associations(question, records)
    for match in matches:
        if match.kind != "exact":
            return [], match
    pinned: list[str] = []
    for match in matches:
        if match.chunk_id not in pinned:
            pinned.append(match.chunk_id)
    return pinned[:MAX_PINNED], None


def find_association(question: str) -> CodeMatch | None:
    """First code in the question, or None. Kept for simple callers and tests."""
    try:
        records = association_records()
    except Exception:
        return None
    return match_association(question, records) if records else None


def clarification(match: CodeMatch) -> tuple[str, list[str]]:
    """Return the 'Did you mean' reply and one follow-up question per choice."""
    lines = []
    for code, name in match.choices:
        lines.append(f"- {code}: {name}" if name else f"- {code}")
    if len(match.choices) == 1:
        code, name = match.choices[0]
        intro = f"I could not find an association with the code {match.typed}. Did you mean {code}"
        intro += f", the {name}?" if name else "?"
        body = intro
    else:
        body = (
            f"I could not find an association with the code {match.typed} on its own. "
            "Did you mean one of these?\n\n" + "\n".join(lines)
        )
    suggestions = [f"What is association {code}?" for code, _ in match.choices]
    return body, suggestions
