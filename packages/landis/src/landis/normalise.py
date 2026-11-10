"""
Section-aware chunks from OCR page blocks.

Running heads are dropped. A table stays inside one chunk. Series and
complex headings come from the wording, not from Markdown heading level,
because OCR assigns ``#``, ``##`` and ``###`` inconsistently.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any

_SERIES = re.compile(r"^(.+?)\s+(Series|Complex)$", re.IGNORECASE)
_LANDSCAPE = re.compile(r"^SOILS OF THE\b", re.IGNORECASE)
_CHAPTER = re.compile(
    r"^(Chapter\s+[IVXLC\d]+|Appendix\b|References\b|Index\b)",
    re.IGNORECASE,
)
_BARE_NUMBER = re.compile(r"^\d{1,4}$")


@dataclass
class ChunkDraft:
    """One passage ready to embed. ``text`` is what the assistant cites."""

    chunk_id: str
    source_doc_id: str
    section: str
    text: str
    table_html: str = ""
    scan_pages: list[int] = field(default_factory=list)
    printed_pages: list[str] = field(default_factory=list)
    entity_codes: list[str] = field(default_factory=list)
    has_table: bool = False

    @property
    def page_start(self) -> int:
        return self.scan_pages[0] if self.scan_pages else 1

    @property
    def page_end(self) -> int:
        return self.scan_pages[-1] if self.scan_pages else self.page_start


@dataclass
class _Part:
    kind: str
    text: str
    html: str
    scan_page: int
    printed_page: str
    section: str


def _words(text: str) -> list[str]:
    return [part for part in (text or "").split() if part]


def _printed_on_page(page: dict[str, Any]) -> int | None:
    for source in (
        page.get("header"),
        page.get("footer"),
    ):
        text = str(source or "").strip()
        if _BARE_NUMBER.fullmatch(text):
            return int(text)
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") not in {"header", "footer"}:
            continue
        text = str(block.get("content") or "").strip()
        if _BARE_NUMBER.fullmatch(text):
            return int(text)
    return None


def printed_page_map(pages: list[dict[str, Any]]) -> dict[int, str]:
    """
    Map scan page to printed page.

    Pages with no printed number (often a chapter opening) take the offset
    seen on neighbouring numbered pages.
    """
    known: dict[int, int] = {}
    numbers: list[int] = []
    for page in pages:
        scan = int(page.get("page_number") or (int(page.get("index") or 0) + 1))
        numbers.append(scan)
        printed = _printed_on_page(page)
        if printed is not None:
            known[scan] = printed
    if not numbers:
        return {}
    offset = 0
    if known:
        offset = int(median([scan - printed for scan, printed in known.items()]))
    mapped: dict[int, str] = {}
    for scan in numbers:
        if scan in known:
            mapped[scan] = str(known[scan])
        elif known:
            mapped[scan] = str(scan - offset)
        else:
            mapped[scan] = ""
    return mapped


def _section_name(text: str) -> str | None:
    cleaned = " ".join((text or "").split())
    if not cleaned or len(cleaned) > 120:
        return None
    if _SERIES.match(cleaned) or _LANDSCAPE.match(cleaned) or _CHAPTER.match(cleaned):
        return cleaned
    return None


def _entity_for(section: str) -> list[str]:
    match = _SERIES.match(section.strip())
    if not match:
        return []
    return [f"series:{match.group(1).strip()}"]


def _table_html(page: dict[str, Any], block: dict[str, Any]) -> str:
    table_id = str(block.get("table_id") or "")
    for table in page.get("tables") or []:
        if not isinstance(table, dict):
            continue
        identifier = str(table.get("id") or table.get("table_id") or "")
        if table_id and identifier == table_id:
            return str(table.get("content") or table.get("html") or "")
    return str(block.get("content") or "")


def _parts_from_blocks(page: dict[str, Any], printed: str) -> list[_Part]:
    scan = int(page.get("page_number") or (int(page.get("index") or 0) + 1))
    section = ""
    parts: list[_Part] = []
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "text")
        content = str(block.get("content") or "").strip()
        if kind == "header":
            continue
        if kind == "footer" and (not content or _BARE_NUMBER.fullmatch(content)):
            continue
        if kind in {"title", "text"}:
            named = _section_name(content)
            if named:
                section = named
                parts.append(_Part("text", named, "", scan, printed, section))
                continue
        if kind == "table":
            html = _table_html(page, block)
            text = content or "Table"
            parts.append(_Part("table", text, html, scan, printed, section))
            continue
        if kind == "image":
            continue
        if content:
            parts.append(_Part("text", content, "", scan, printed, section))
    return parts


def _parts_from_markdown(page: dict[str, Any], printed: str) -> list[_Part]:
    scan = int(page.get("page_number") or (int(page.get("index") or 0) + 1))
    section = ""
    parts: list[_Part] = []
    for line in str(page.get("markdown") or "").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("!["):
            continue
        named = _section_name(cleaned.lstrip("#").strip())
        if named:
            section = named
        parts.append(_Part("text", cleaned.lstrip("#").strip(), "", scan, printed, section))
    return parts


def _flush(buffer: list[_Part], doc_id: str, counters: dict[int, int]) -> ChunkDraft | None:
    if not buffer:
        return None
    texts = [part.text for part in buffer if part.text]
    if not texts:
        return None
    scan = buffer[0].scan_page
    counters[scan] = counters.get(scan, 0) + 1
    section = buffer[-1].section or buffer[0].section
    html = "\n".join(part.html for part in buffer if part.html)
    pages = []
    printed = []
    for part in buffer:
        if part.scan_page not in pages:
            pages.append(part.scan_page)
        if part.printed_page and part.printed_page not in printed:
            printed.append(part.printed_page)
    return ChunkDraft(
        chunk_id=f"{doc_id}-{scan:03d}-{counters[scan]:02d}",
        source_doc_id=doc_id,
        section=section,
        text="\n\n".join(texts).strip(),
        table_html=html,
        scan_pages=pages,
        printed_pages=printed,
        entity_codes=_entity_for(section),
        has_table=any(part.kind == "table" for part in buffer),
    )


def _split_long_text(part: _Part, max_words: int) -> list[_Part]:
    words = _words(part.text)
    if len(words) <= max_words:
        return [part]
    pieces: list[_Part] = []
    start = 0
    while start < len(words):
        window = words[start : start + max_words]
        pieces.append(
            _Part(part.kind, " ".join(window), "", part.scan_page, part.printed_page, part.section)
        )
        start += max_words
    return pieces


def chunks_from_pages(
    pages: list[dict[str, Any]],
    *,
    doc_id: str,
    min_words: int = 300,
    max_words: int = 800,
    structured: bool = False,
) -> list[ChunkDraft]:
    """
    Build chunks for one document.

    ``structured`` keeps each page as its own chunk (database-style records
    and short references). Prose is grouped by section and split at
    ``max_words``, without cutting a table in half.
    """
    printed_map = printed_page_map(pages)
    if structured:
        counters: dict[int, int] = {}
        chunks: list[ChunkDraft] = []
        for page in pages:
            scan = int(page.get("page_number") or (int(page.get("index") or 0) + 1))
            parts = _parts_from_blocks(page, printed_map.get(scan, "")) or _parts_from_markdown(
                page, printed_map.get(scan, "")
            )
            draft = _flush(parts, doc_id, counters)
            if draft:
                chunks.append(draft)
        return chunks

    stream: list[_Part] = []
    for page in pages:
        scan = int(page.get("page_number") or (int(page.get("index") or 0) + 1))
        printed = printed_map.get(scan, "")
        blocks = page.get("blocks") or []
        page_parts = _parts_from_blocks(page, printed) if blocks else _parts_from_markdown(page, printed)
        stream.extend(page_parts)

    counters = {}
    chunks = []
    buffer: list[_Part] = []
    words = 0
    section = ""

    def emit() -> None:
        nonlocal buffer, words
        draft = _flush(buffer, doc_id, counters)
        if draft:
            chunks.append(draft)
        buffer = []
        words = 0

    for part in stream:
        if part.section and part.section != section and buffer:
            emit()
        section = part.section or section
        part.section = section
        if part.kind == "table":
            if buffer and words >= min_words:
                emit()
            buffer.append(part)
            emit()
            continue
        for piece in _split_long_text(part, max_words):
            piece_words = len(_words(piece.text))
            if buffer and words >= min_words and words + piece_words > max_words:
                emit()
            buffer.append(piece)
            words += piece_words
    emit()
    return [chunk for chunk in chunks if chunk.text.strip()]


def chunks_from_csv_text(doc_id: str, csv_text: str) -> list[ChunkDraft]:
    """One chunk per data row. The header is repeated so each chunk stands alone."""
    import csv
    from io import StringIO

    reader = csv.reader(StringIO(csv_text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        return []
    header = rows[0]
    chunks: list[ChunkDraft] = []
    for index, row in enumerate(rows[1:], start=1):
        pairs = []
        for name, value in zip(header, row):
            if value.strip():
                pairs.append(f"{name.strip()}: {value.strip()}")
        text = "\n".join(pairs)
        if not text:
            continue
        chunks.append(
            ChunkDraft(
                chunk_id=f"{doc_id}-001-{index:02d}",
                source_doc_id=doc_id,
                section=header[0].strip() if header else "",
                text=text,
                scan_pages=[1],
                printed_pages=[],
            )
        )
    return chunks


def chunks_from_plain_text(
    doc_id: str,
    text: str,
    *,
    max_words: int = 500,
    overlap_words: int = 100,
) -> list[ChunkDraft]:
    """Overlapping word windows for notes that were not OCRed."""
    words = _words(text)
    if not words:
        return []
    stride = max(1, max_words - overlap_words)
    chunks: list[ChunkDraft] = []
    start = 0
    index = 1
    while start < len(words):
        window = words[start : start + max_words]
        chunks.append(
            ChunkDraft(
                chunk_id=f"{doc_id}-001-{index:02d}",
                source_doc_id=doc_id,
                section="",
                text=" ".join(window),
                scan_pages=[1],
            )
        )
        index += 1
        if start + max_words >= len(words):
            break
        start += stride
    return chunks


def embedding_prefix(row: dict[str, str], section: str) -> str:
    """Short context placed in front of the passage before it is embedded."""
    title = (row.get("title") or row.get("doc_id") or "LandIS document").strip()
    year = (row.get("publication_year") or "").strip()
    year_bit = f" ({year})" if year else ""
    section_bit = f", {section}" if section else ""
    return f"{title}{year_bit}{section_bit}."
