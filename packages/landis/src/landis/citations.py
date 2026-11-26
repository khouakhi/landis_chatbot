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


MAX_MARKERS_PER_GROUP = 2
_MARKER = re.compile(r"(\s*)\[([^\[\]]+)\]")
_CODE_LIKE = re.compile(r"^\d+[A-Za-z]?$")


def clean_citation_markers(answer: str, max_label: int) -> str:
    """Remove numbers in square brackets that are not excerpt numbers.

    The model sometimes puts soil association codes such as ``541m`` inside the
    citation brackets. A bracket group made only of numbers, or numbers with a
    single trailing letter, is a citation group. Only whole numbers from 1 to
    ``max_label`` are kept, at most two per group. A group left with none is removed. Other bracketed
    text, such as Markdown links, is not touched.
    """
    if not answer or max_label < 1:
        return answer

    def fix(match: re.Match) -> str:
        tokens = [part.strip() for part in match.group(2).split(",")]
        if not all(_CODE_LIKE.match(token) for token in tokens):
            return match.group(0)
        valid: list[str] = []
        for token in tokens:
            if token.isdigit() and 1 <= int(token) <= max_label and token not in valid:
                valid.append(token)
        # Two excerpts per statement at most. The model often cites more than it uses.
        valid = valid[:MAX_MARKERS_PER_GROUP]
        if not valid:
            return ""
        return f"{match.group(1)}[{', '.join(valid)}]"

    return _MARKER.sub(fix, answer)


_MORE_DETAIL = re.compile(
    r"^\s*(?:\*\*)?More detail is in the LandIS Soils Guide\b.*$\n?",
    re.IGNORECASE | re.MULTILINE,
)


def strip_more_detail_lines(answer: str) -> str:
    """Remove a 'More detail' line the model wrote. The chat adds the linked line itself."""
    cleaned = _MORE_DETAIL.sub("", answer or "")
    return re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()


def ensure_citation(answer: str, label: int) -> str:
    """Add ``[label]`` to the last prose line if no marker already cites that excerpt.

    Table rows are skipped so that a marker never breaks a table.
    """
    if not answer.strip():
        return answer
    for match in _MARKER.finditer(answer):
        if str(label) in [part.strip() for part in match.group(2).split(",")]:
            return answer
    lines = answer.rstrip().split("\n")
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() and not lines[index].lstrip().startswith("|"):
            lines[index] = f"{lines[index].rstrip()} [{label}]"
            break
    return "\n".join(lines)


SOILS_GUIDE = "LandIS Soils Guide"
MAX_LINKS = 3


def more_detail_line(sources: list[SourceRef]) -> str:
    """A closing line that links each cited database record to its Soils Guide page."""
    seen: set[str] = set()
    links: list[str] = []
    for source in sources:
        url = (source.record_url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        label = (source.record_title or "").strip() or SOILS_GUIDE
        links.append(f"[{label}]({url})")
        if len(links) == MAX_LINKS:
            break
    if not links:
        return ""
    return f"More detail is in the {SOILS_GUIDE}: " + ", ".join(links) + "."


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
