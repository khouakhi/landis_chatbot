"""Tidy Markdown slips in model answers.

The model sometimes bolds a whole sentence, bolds a phrase in the middle of a
sentence, or ends a sentence with a colon (".:"). Bold is kept for two things
only: a section title on its own line, and a label at the start of a line or
bullet such as ``**Name:**``. Everything else bold is turned back into plain text.

**Created:** 25-11-2026 (UK style).
"""

from __future__ import annotations

import re

MAX_TITLE_WORDS = 8
MAX_HEADING_WORDS = 6

_CITATIONS = r"(?P<cite>(?:\s*\[\d+(?:\s*,\s*\d+)*\])*)"
_BOLD_ONLY = re.compile(
    r"^(?P<prefix>\s*(?:[-*]\s+)?)\*\*(?P<text>[^*]+)\*\*(?P<colon>\s*:)?" + _CITATIONS + r"\s*$"
)
_MARKER_ONLY = re.compile(r"^\s*(?:\[\d+(?:\s*,\s*\d+)*\]\s*)+$")
_SENTENCE_COLON = re.compile(r"(?P<stop>[.!?])\s*:" + _CITATIONS + r"\s*$")


def _is_sentence(text: str) -> bool:
    plain = text.strip().rstrip(":").strip()
    return plain.endswith((".", "!")) or len(plain.split()) > MAX_TITLE_WORDS


_HEADING_LINE = re.compile(r"^\s*\*\*[^*\n]{1,80}\*\*:?\s*$")  # no bullet marker
_INLINE_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_PLAIN_HEADING = re.compile(r"^(?P<indent>\s*)(?:[-*]\s+)?(?P<text>[A-Z][^*\[\]|#:]*):\s*$")


def _is_plain_heading(line: str) -> bool:
    """A short line ending in a colon, such as ``Overview:``, is a section title.

    The model does not always bold its titles, or sometimes writes a title as a
    bullet with nothing after the colon. Either way the title is made bold here.
    Lines that start with a code (``HOST12``), bullets with text after the colon and
    longer sentences stay as they are.
    """
    match = _PLAIN_HEADING.match(line)
    if not match:
        return False
    words = match.group("text").split()
    if not 1 <= len(words) <= MAX_HEADING_WORDS:
        return False
    return not any(char.isdigit() for char in words[0])


def _unbold_inline(text: str) -> str:
    """Remove bold from a phrase or sentence in the middle of a line."""
    return _INLINE_BOLD.sub(lambda match: match.group(1), text)


def _tidy_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("|"):
        # Table cells are plain text too. Bold in a cell reads as random emphasis.
        return _unbold_inline(line)
    if stripped.startswith("#"):
        return line
    bold = _BOLD_ONLY.match(line)
    if bold:
        text = bold.group("text").strip()
        if _is_sentence(text):
            return f"{bold.group('prefix')}{text.rstrip(':').strip()}{bold.group('cite')}"
        if bold.group("prefix").strip():
            # A bullet that holds only a label is a title, so it becomes a bold heading.
            label = f"{text.rstrip(':').strip()}:"
            if _is_plain_heading(label):
                indent = re.match(r"\s*", bold.group("prefix")).group(0)
                return f"{indent}**{label}**"
            # Otherwise a bold bullet is an item: keep the text, drop the bold.
            return f"{bold.group('prefix')}{text}{bold.group('colon') or ''}{bold.group('cite')}"
        return line
    line = _SENTENCE_COLON.sub(lambda match: match.group("stop") + match.group("cite"), line)
    if _HEADING_LINE.match(line):
        return line  # A section title on its own line stays bold.
    if _is_plain_heading(line):
        match = _PLAIN_HEADING.match(line)
        return f"{match.group('indent')}**{match.group('text').strip()}:**"
    return _unbold_inline(line)


def _join_marker_lines(lines: list[str]) -> list[str]:
    """Move a citation marker that sits alone on a line to the end of the line above."""
    joined: list[str] = []
    for line in lines:
        if _MARKER_ONLY.match(line):
            for index in range(len(joined) - 1, -1, -1):
                if joined[index].strip():
                    joined[index] = f"{joined[index].rstrip()} {line.strip()}"
                    break
            else:
                joined.append(line)
        else:
            joined.append(line)
    return joined


def tidy_answer_format(answer: str) -> str:
    """Unbold sentence-like bold lines, drop ".:" and attach lone citation markers."""
    if not answer:
        return answer
    lines = _join_marker_lines([_tidy_line(line) for line in answer.split("\n")])
    return "\n".join(lines).rstrip()
