"""
Turn a Mistral OCR response into the cleaned-folder layout.

Keys are normalised in one place. The playground uses camelCase
(``averagePageConfidenceScore``); the API uses snake_case.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

PLAIN_TEXT_SUFFIXES = frozenset({".md", ".txt", ".html"})


def snake_case(key: str) -> str:
    """Convert a camelCase key to snake_case. Snake_case keys are unchanged."""
    if not key or "_" in key:
        return key
    step = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    step = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step)
    return step.lower()


def normalise_keys(value: Any) -> Any:
    """Recursively rename mapping keys to snake_case."""
    if isinstance(value, dict):
        return {snake_case(str(key)): normalise_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalise_keys(item) for item in value]
    return value


def page_number(page: dict[str, Any]) -> int:
    """1-based scan page. Mistral ``index`` is 0-based."""
    index = page.get("index")
    if index is None:
        return 1
    return int(index) + 1


def write_cleaned_document(dest_root: Path, doc_id: str, payload: dict[str, Any]) -> Path:
    """
    Write ``02_Source_cleaned/<doc_id>/`` from one OCR payload.

    A ``CURATED`` marker in that folder stops the write, so a reviewed
    document is not replaced by a later OCR run.
    """
    normalised = normalise_keys(payload)
    dest = dest_root / doc_id
    if (dest / "CURATED").is_file():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    pages = normalised.get("pages") or []
    if not isinstance(pages, list):
        pages = []

    parts: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        number = page_number(page)
        folder = dest / "pages" / f"page-{number}"
        folder.mkdir(parents=True, exist_ok=True)
        markdown = str(page.get("markdown") or "")
        header = page.get("header")
        footer = page.get("footer")
        (folder / "markdown.md").write_text(markdown, encoding="utf-8")
        (folder / "header.md").write_text("" if header is None else str(header), encoding="utf-8")
        (folder / "footer.md").write_text("" if footer is None else str(footer), encoding="utf-8")
        metadata = {
            "index": page.get("index"),
            "page_number": number,
            "dimensions": page.get("dimensions"),
            "confidence_scores": page.get("confidence_scores"),
            "blocks": page.get("blocks") or [],
            "tables": page.get("tables") or [],
            "header": header,
            "footer": footer,
        }
        (folder / "page-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if markdown.strip():
            parts.append(markdown.strip())

    (dest / "markdown.md").write_text("\n\n".join(parts) + ("\n" if parts else ""), encoding="utf-8")
    (dest / "raw_response.json").write_text(
        json.dumps(normalised, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dest


def copy_plain_text(dest_root: Path, doc_id: str, source: Path) -> Path | None:
    """Copy a Markdown, text, or HTML file into ``02_Source_cleaned/<doc_id>/``.

    These files are not OCRed. A ``CURATED`` marker stops the copy.
    """
    if source.suffix.lower() not in PLAIN_TEXT_SUFFIXES or not source.is_file():
        return None
    dest = dest_root / doc_id
    if (dest / "CURATED").is_file():
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / source.name
    if target.resolve() != source.resolve():
        shutil.copy2(source, target)
    return target


def load_page_metadata(doc_dir: Path) -> list[dict[str, Any]]:
    """Load per-page metadata in scan order. This is the input to chunking."""
    pages_root = doc_dir / "pages"
    if not pages_root.is_dir():
        return []
    folders = sorted(pages_root.glob("page-*"), key=lambda path: path.name)
    pages: list[dict[str, Any]] = []
    for folder in folders:
        path = folder / "page-metadata.json"
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        markdown_path = folder / "markdown.md"
        if markdown_path.is_file():
            data["markdown"] = markdown_path.read_text(encoding="utf-8")
        pages.append(normalise_keys(data))
    return pages


def count_pages(payload: dict[str, Any]) -> int:
    usage = payload.get("usage_info") or payload.get("usage") or {}
    if isinstance(usage, dict):
        for key in ("pages_processed", "page_count", "pages"):
            if isinstance(usage.get(key), int):
                return int(usage[key])
    pages = payload.get("pages") or []
    return len(pages) if isinstance(pages, list) else 0
