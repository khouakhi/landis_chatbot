"""
Build index rows from the corpus register and cleaned OCR output.

Phase 3 is disposable: re-run this when chunking or the embedding model
changes. Do not re-run OCR just to rebuild the index.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from .normalise import (
    ChunkDraft,
    chunks_from_csv_text,
    chunks_from_pages,
    chunks_from_plain_text,
    embedding_prefix,
)
from .ocr_layout import load_page_metadata
from .register import doc_type_for_row, resolve_source_file


def has_index_input(knowledge_base: Path, row: dict[str, str]) -> bool:
    """True when OCR output, or a text/CSV original, is available to chunk."""
    doc_id = row.get("doc_id") or ""
    if (knowledge_base / "02_Source_cleaned" / doc_id / "raw_response.json").is_file():
        return True
    path = resolve_source_file(knowledge_base, row.get("file_name", ""))
    return path is not None and path.suffix.lower() in cfg.TEXT_EXTENSIONS


def uses_structured_chunks(row: dict[str, str]) -> bool:
    """A set chunking mode wins. Otherwise every prose type uses section grouping."""
    mode = (row.get("chunking_mode") or "").strip()
    if mode:
        return mode == "structured - one chunk per record"
    return False


def skips_prose_chunking(row: dict[str, str]) -> bool:
    """Maps are not prose-chunked, unless a person has set chunking_mode."""
    if (row.get("chunking_mode") or "").strip():
        return False
    return doc_type_for_row(row) == "map"


def drafts_for_row(knowledge_base: Path, row: dict[str, str]) -> list[ChunkDraft]:
    """Chunk one register row. Raises FileNotFoundError if nothing has been prepared."""
    doc_id = row["doc_id"]
    if skips_prose_chunking(row):
        return []
    structured = uses_structured_chunks(row)
    cleaned = knowledge_base / "02_Source_cleaned" / doc_id
    if (cleaned / "raw_response.json").is_file():
        pages = load_page_metadata(cleaned)
        return chunks_from_pages(
            pages,
            doc_id=doc_id,
            min_words=cfg.CHUNK_MIN_WORDS,
            max_words=cfg.CHUNK_MAX_WORDS,
            structured=structured,
        )
    path = resolve_source_file(knowledge_base, row.get("file_name", ""))
    if path is None:
        raise FileNotFoundError(f"{doc_id} has no OCR output and the source file was not found.")
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".csv" or structured:
        drafts = chunks_from_csv_text(doc_id, text)
        if drafts:
            return drafts
    return chunks_from_plain_text(
        doc_id,
        text,
        max_words=cfg.CHUNK_FALLBACK_WORDS,
        overlap_words=cfg.CHUNK_OVERLAP_WORDS,
    )


def document_record(row: dict[str, str], source_path: str, file_hash: str) -> dict:
    """Postgres document row. A public URL is exposed only for an open licence."""
    public_url = (row.get("public_url") or "").strip()
    year = (row.get("publication_year") or "").strip()
    open_licence = row.get("licence") == "open"
    return {
        "doc_id": row["doc_id"],
        "source_path": source_path,
        "file_hash": file_hash,
        "title": (row.get("title") or "").strip() or None,
        "public_url": public_url or None,
        "is_public": bool(public_url) and open_licence,
        "publication_year": int(year) if year.isdigit() else None,
        "document_type": (row.get("document_type") or "").strip() or None,
        "authority_tier": (row.get("authority_tier") or "").strip() or None,
        "licence": (row.get("licence") or "").strip() or None,
        "superseded": row.get("superseded") == "yes",
        "place": (row.get("place") or "").strip() or None,
        "spatial_scale": (row.get("spatial_scale") or "").strip() or None,
        "chunking_mode": (row.get("chunking_mode") or "").strip()
        or (None if skips_prose_chunking(row) else "prose - section level"),
        "pipeline_status": "indexed",
    }


def texts_to_embed(row: dict[str, str], drafts: list[ChunkDraft]) -> list[str]:
    """Passage text with a short title prefix. The stored chunk text stays unprefixed."""
    prefixed = []
    for draft in drafts:
        prefix = embedding_prefix(row, draft.section)
        prefixed.append(f"{prefix}\n{draft.text}")
    return prefixed


def chunk_records(
    row: dict[str, str],
    drafts: list[ChunkDraft],
    embeddings,
    *,
    source_path: str,
    file_hash: str,
) -> list[dict]:
    records = []
    for index, draft in enumerate(drafts):
        vector = embeddings[index]
        records.append(
            {
                "chunk_id": draft.chunk_id,
                "doc_id": row["doc_id"],
                "source_path": source_path,
                "source_hash": file_hash,
                "chunk_index": index,
                "section": draft.section or None,
                "text": draft.text,
                "table_html": draft.table_html or None,
                "entity_codes": "; ".join(draft.entity_codes) or None,
                "page_start": draft.page_start,
                "page_end": draft.page_end,
                "printed_page_start": draft.printed_pages[0] if draft.printed_pages else None,
                "printed_page_end": draft.printed_pages[-1] if draft.printed_pages else None,
                "has_table": draft.has_table,
                "embedding": vector.tolist(),
            }
        )
    return records


def build_manifest(*, documents: int, chunks: int, doc_ids: list[str]) -> dict:
    return {
        "kb_version": cfg.KB_VERSION,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "embedding_model": cfg.MISTRAL_EMBED_MODEL,
        "embedding_dimension": cfg.EMBEDDING_DIMENSION,
        "chat_model": cfg.MISTRAL_CHAT_MODEL,
        "ocr_model": cfg.OCR_MODEL,
        "documents": documents,
        "chunks": chunks,
        "doc_ids": doc_ids,
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
