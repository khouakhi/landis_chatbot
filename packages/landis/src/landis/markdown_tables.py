"""
Normalise Markdown tables that models emit on a single line.

Adapted from the SOILL assistant. Safe to call on any answer.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re

_SEP_RE = re.compile(
    r"\|[\t ]*[-:]{2,}[\t :|-]*(?:\|[\t ]*[-:]{2,}[\t :|-]*)*\|?"
)
_TABLE_START_RE = re.compile(r"\|\s*[^|\n]+\s*\|\s*[^|\n]+\s*\|")


def _parse_cells(line: str) -> list[str]:
    trimmed = (line or "").strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _format_row(cells: list[str]) -> str:
    return "| " + " | ".join(cell or "" for cell in cells) + " |"


def _expand_collapsed_table_line(line: str) -> str:
    trimmed = (line or "").strip()
    if trimmed.count("|") < 6 or not re.search(r"-{2,}", trimmed):
        return line

    table_match = _TABLE_START_RE.search(trimmed)
    prefix = ""
    if table_match and table_match.start() > 0:
        prefix = trimmed[: table_match.start()].strip()
        trimmed = trimmed[table_match.start() :].strip()

    sep_match = _SEP_RE.search(trimmed)
    if not sep_match:
        return line

    before = trimmed[: sep_match.start()].strip()
    after = trimmed[sep_match.end() :].strip()
    if not before or not after:
        return line

    header_cells = _parse_cells(before if before.startswith("|") else f"| {before}")
    while len(header_cells) > 1 and not header_cells[-1]:
        header_cells.pop()
    col_count = len(header_cells)
    if col_count < 2:
        return line

    rows = [
        _format_row(header_cells),
        _format_row(["---"] * col_count),
    ]

    if "||" in after:
        parts = [part.strip() for part in after.split("||") if part.strip()]
        for part in parts:
            row = part
            if not row.startswith("|"):
                row = f"| {row}"
            if not row.endswith("|"):
                row = f"{row} |"
            cells = _parse_cells(row)
            if not any(cells):
                continue
            while len(cells) < col_count:
                cells.append("")
            rows.append(_format_row(cells[:col_count]))
    else:
        body_cells = _parse_cells(after if after.startswith("|") else f"| {after}")
        cleaned: list[str] = []
        for i, cell in enumerate(body_cells):
            at_row_start = len(cleaned) % col_count == 0
            if (
                at_row_start
                and not cell
                and i + 1 < len(body_cells)
                and body_cells[i + 1]
            ):
                continue
            cleaned.append(cell)
        for i in range(0, len(cleaned), col_count):
            chunk = cleaned[i : i + col_count]
            if not any(chunk):
                continue
            while len(chunk) < col_count:
                chunk.append("")
            rows.append(_format_row(chunk))

    if len(rows) < 3:
        return line

    table = "\n".join(rows)
    if prefix:
        return f"{prefix}\n\n{table}"
    return table


def normalise_markdown_tables(text: str) -> str:
    """
    Expand single-line / ||-joined Markdown tables into one row per line.

    Safe to call on any assistant answer; leaves non-table text unchanged.
    """
    value = (text or "").replace("\r\n", "\n").strip()
    if not value or "|" not in value:
        return text or ""

    expanded_lines = [_expand_collapsed_table_line(line) for line in value.split("\n")]
    return "\n".join(expanded_lines)
