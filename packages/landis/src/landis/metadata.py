"""
Pre-fill register fields from OCR pages.

Filled-in human values are left alone. Subject classes are never guessed.
Anything written here is marked unverified in ``metadata_note``.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re
from typing import Any

_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_RECORD = re.compile(r"\bRecord\s+No\.?\s*(\d+)\b", re.IGNORECASE)
_SERIES = re.compile(r"^(.+?)\s+(Series|Complex)$", re.IGNORECASE)
_SOILS_IN = re.compile(
    r"^(?:#+\s*)?Soils in ([A-Z][A-Za-z &'()-]{2,60})",
    re.MULTILINE,
)


def _heading(page: dict[str, Any]) -> str:
    for block in page.get("blocks") or []:
        if isinstance(block, dict) and block.get("type") in {"title", "header"}:
            text = str(block.get("content") or "").strip()
            if text and not text.isdigit() and len(text.split()) <= 16:
                return text
    for line in str(page.get("markdown") or "").splitlines():
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned
    return ""


def series_codes(pages: list[dict[str, Any]]) -> list[str]:
    """``series:Name`` codes from title blocks. Order is first appearance."""
    found: list[str] = []
    seen: set[str] = set()
    for page in pages:
        candidates: list[str] = []
        for block in page.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "title":
                candidates.append(str(block.get("content") or "").strip())
        for line in str(page.get("markdown") or "").splitlines():
            candidates.append(line.strip().lstrip("#").strip())
        for text in candidates:
            match = _SERIES.match(text)
            if not match:
                continue
            name = match.group(1).strip()
            code = f"series:{name}"
            key = code.lower()
            if key not in seen and name:
                seen.add(key)
                found.append(code)
    return found


def suggest_metadata(pages: list[dict[str, Any]]) -> dict[str, str]:
    """Suggestions only. The caller decides which empty fields to fill."""
    first = pages[0] if pages else {}
    text = "\n".join(str(page.get("markdown") or "") for page in pages[:2])
    title = _heading(first)
    year_match = _YEAR.search(text)
    record_match = _RECORD.search(text)
    place_match = _SOILS_IN.search(text)
    return {
        "title": title,
        "publication_year": year_match.group(1) if year_match else "",
        "record_no": record_match.group(1) if record_match else "",
        "place": place_match.group(1).strip(" ,") if place_match else "",
        "entity_codes": "; ".join(series_codes(pages)),
    }


def _append_note(existing: str, extra: str) -> str:
    if not extra:
        return existing
    if not existing:
        return extra
    if extra in existing:
        return existing
    return f"{existing}; {extra}"


def apply_prefill(row: dict[str, str], pages: list[dict[str, Any]]) -> dict[str, str]:
    """
    Fill blank descriptive fields. Does not change subject classes or a
    value a person has already entered.
    """
    updated = dict(row)
    suggestions = suggest_metadata(pages)
    notes: list[str] = []
    if not (updated.get("title") or "").strip() and suggestions["title"]:
        updated["title"] = suggestions["title"]
        notes.append("title pre-filled, unverified")
    if not (updated.get("publication_year") or "").strip() and suggestions["publication_year"]:
        updated["publication_year"] = suggestions["publication_year"]
        notes.append("publication year pre-filled, unverified")
    if not (updated.get("place") or "").strip() and suggestions["place"]:
        updated["place"] = suggestions["place"]
        notes.append("place pre-filled, unverified")
    if not (updated.get("entity_codes") or "").strip() and suggestions["entity_codes"]:
        updated["entity_codes"] = suggestions["entity_codes"]
        notes.append("entity codes pre-filled, unverified")
    if suggestions["record_no"]:
        notes.append(f"record number seen in OCR: {suggestions['record_no']} (unverified)")
    updated["metadata_note"] = _append_note(updated.get("metadata_note") or "", "; ".join(notes))
    return updated
