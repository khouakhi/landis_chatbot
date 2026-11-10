"""
Flag OCR pages for a person to check.

Page-minimum confidence is not used on its own. The pilot on a 108-page
Soil Survey Record showed that it flagged most pages and was not useful.
A page is flagged when more than N words fall below a threshold.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(r"^\d+(\.\d+)?$")


def iter_word_scores(container: dict[str, Any]) -> list[tuple[str, float]]:
    """Read word scores whether the payload used ``confidence`` or ``score``."""
    scores = container.get("confidence_scores") or {}
    if not isinstance(scores, dict):
        scores = {}
    words = scores.get("word_confidence_scores") or container.get("word_confidence_scores") or []
    found: list[tuple[str, float]] = []
    if not isinstance(words, list):
        return found
    for item in words:
        if isinstance(item, dict):
            raw = item.get("confidence", item.get("score", item.get("confidence_score")))
            text = str(item.get("word") or item.get("text") or "")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            text, raw = str(item[0]), item[1]
        else:
            continue
        if raw is None:
            continue
        try:
            found.append((text, float(raw)))
        except (TypeError, ValueError):
            continue
    return found


def _word_count(text: str) -> int:
    return len([part for part in (text or "").split() if part.strip()])


def review_page(
    page: dict[str, Any],
    *,
    low_confidence: float = 0.6,
    max_low_words: int = 5,
    near_empty_words: int = 8,
) -> list[str]:
    """Reasons to review one page. An empty list means no flag."""
    number = int(page.get("page_number") or (int(page.get("index") or 0) + 1))
    flags: list[str] = []
    words = iter_word_scores(page)
    low = [text for text, score in words if score < low_confidence]
    if len(low) > max_low_words:
        flags.append(
            f"page {number}: {len(low)} words below {low_confidence:.2f} confidence"
        )

    for table in page.get("tables") or []:
        if not isinstance(table, dict):
            continue
        for text, score in iter_word_scores(table):
            if _NUMBER.fullmatch(text.strip()) and score < low_confidence:
                flags.append(
                    f"page {number}: numeric table cell {text!r} below {low_confidence:.2f}"
                )
                break

    blocks = page.get("blocks") or []
    has_image = any(isinstance(block, dict) and block.get("type") == "image" for block in blocks)
    if not has_image and _word_count(str(page.get("markdown") or "")) < near_empty_words:
        flags.append(f"page {number}: near-empty and not an image or plate page")
    return flags


def review_document(
    pages: list[dict[str, Any]],
    *,
    low_confidence: float = 0.6,
    max_low_words: int = 5,
    near_empty_words: int = 8,
    register_year: str = "",
) -> list[str]:
    """Flags for a whole document, including a year that disagrees with the register."""
    flags: list[str] = []
    for page in pages:
        flags.extend(
            review_page(
                page,
                low_confidence=low_confidence,
                max_low_words=max_low_words,
                near_empty_words=near_empty_words,
            )
        )
    years: set[str] = set()
    first = pages[0] if pages else {}
    years.update(
        match.group(0)
        for match in re.finditer(r"\b(?:19|20)\d{2}\b", str(first.get("markdown") or ""))
    )
    cleaned_year = (register_year or "").strip()
    if cleaned_year and years and cleaned_year not in years and len(years) == 1:
        flags.append(
            f"publication year {cleaned_year} disagrees with the year printed in the OCR text"
        )
    return flags
