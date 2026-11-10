"""
Parse citation markers and the suggested-question block.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re

from .rag import SourceRef

_SUGGESTED = re.compile(
    r"<<SUGGESTED>>\s*(.*?)\s*<<END>>\s*$",
    re.DOTALL | re.IGNORECASE,
)


def split_suggested_questions(answer: str) -> tuple[str, list[str]]:
    """Split the answer from an optional ``<<SUGGESTED>>`` block. At most three questions."""
    text = (answer or "").strip()
    if not text:
        return "", []
    match = _SUGGESTED.search(text)
    if not match:
        return text, []
    questions: list[str] = []
    for line in match.group(1).splitlines():
        cleaned = line.strip()
        if cleaned.startswith(("- ", "* ")):
            cleaned = cleaned[2:].strip()
        else:
            cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
        if cleaned:
            questions.append(cleaned)
    return text[: match.start()].strip(), questions[:3]


def sources_cited_in_answer(answer: str, sources: list[SourceRef]) -> list[SourceRef]:
    """Keep sources whose numbers appear as ``[1]`` or ``[2, 3]`` in the answer."""
    if not sources:
        return []
    max_label = max(source.label for source in sources)
    cited: set[int] = set()
    for match in re.finditer(r"\[([^\]]+)\]", answer or ""):
        for part in match.group(1).split(","):
            token = part.strip()
            if token.isdigit():
                number = int(token)
                if 1 <= number <= max_label:
                    cited.add(number)
    by_label = {source.label: source for source in sources}
    return [by_label[number] for number in sorted(cited) if number in by_label]
