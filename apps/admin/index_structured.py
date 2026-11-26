"""Embed and load the structured database records (KB-103) into Postgres.

Reads the JSON Lines files in ``knowledge-base/03_Structured_data/landis_db/records/``
and stores one document with one chunk per record. Dry run by default: nothing is
sent to Mistral and nothing is written until ``--apply`` is given.

Run from the ``code`` folder:

    python -m apps.admin.index_structured            # dry run
    python -m apps.admin.index_structured --apply    # embed and load
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from landis import config as cfg
from landis import store_pg
from landis.embeddings import embed_texts, get_client
from landis.indexing import chunk_records, document_record, texts_to_embed
from landis.normalise import ChunkDraft
from landis.register import (
    already_ingested,
    licence_allows_external_processing,
    load_register,
    save_register,
)
from pipeline.structured_records import RECORD_FILES, load_settings, resolve_path

KB_ID = "KB-103"
# Logical path stored in the database, relative to the knowledge base, so that no
# local user folder is written to the database.
SOURCE_PATH = "03_Structured_data/landis_db/records"
# Structured records have no scan pages. Page 0 means "no page" and the chat
# interface leaves the location out for it.
NO_PAGE = 0


def records_dir() -> Path:
    settings = load_settings()
    return resolve_path(settings["structured_data"]["records_dir"])


def read_records(folder: Path) -> tuple[list[dict], str]:
    """Return every record in file order, and a SHA-256 over the JSONL bytes."""
    digest = hashlib.sha256()
    records: list[dict] = []
    for filename in RECORD_FILES.values():
        raw = (folder / filename).read_bytes()
        digest.update(raw)
        for line in raw.decode("utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records, digest.hexdigest()


def to_drafts(records: list[dict]) -> list[ChunkDraft]:
    drafts = []
    for record in records:
        drafts.append(
            ChunkDraft(
                chunk_id=record["chunk_id"],
                source_doc_id=record["source_doc_id"],
                section=record.get("section") or "",
                text=record["text"],
                entity_codes=list(record.get("entity_codes") or []),
                scan_pages=[NO_PAGE],
                source_url=record.get("source_url") or "",
                source_title=record.get("title") or "",
            )
        )
    return drafts


def check_records(records: list[dict], drafts: list[ChunkDraft], folder: Path) -> list[str]:
    """Return problems that must stop the load. Counts must match the manifest."""
    problems = []
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    expected = sum(info["records"] for info in manifest["files"].values())
    if len(records) != expected:
        problems.append(f"found {len(records)} records, manifest says {expected}")
    ids = [draft.chunk_id for draft in drafts]
    if len(set(ids)) != len(ids):
        problems.append("duplicate chunk_id values")
    if any(record["source_doc_id"] != KB_ID for record in records):
        problems.append(f"a record has a source_doc_id other than {KB_ID}")
    if any(not draft.text.strip() for draft in drafts):
        problems.append("a record has empty text")
    return problems


def existing_chunk_ids(chunk_ids: list[str]) -> list[str]:
    """Chunk IDs already in the database. Read only."""
    with store_pg.get_connection() as conn:
        rows = conn.execute(
            "SELECT chunk_id FROM chunks WHERE chunk_id = ANY(%s)", (chunk_ids,)
        ).fetchall()
    return [row[0] for row in rows]


def update_links(records: list[dict]) -> int:
    """Set source_url and source_title on loaded chunks. Embeddings are not touched."""
    rows = [
        (record.get("source_url") or None, record.get("title") or None, record["chunk_id"])
        for record in records
    ]
    conn = store_pg.get_connection()
    with conn.cursor() as cur:
        cur.executemany(
            "UPDATE chunks SET source_url = %s, source_title = %s WHERE chunk_id = %s", rows
        )
    conn.commit()
    row = conn.execute(
        "SELECT count(*) AS n FROM chunks WHERE doc_id = %s AND source_url IS NOT NULL",
        (KB_ID,),
    ).fetchone()
    return int(row["n"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed and load KB-103 structured records")
    parser.add_argument("--apply", action="store_true", help="Embed and write to the database")
    parser.add_argument(
        "--force", action="store_true", help="Load even if the register says ingested"
    )
    parser.add_argument(
        "--links-only",
        action="store_true",
        help="Only set source_url and source_title on chunks already loaded",
    )
    args = parser.parse_args()

    if args.links_only:
        records, _ = read_records(records_dir())
        print(f"{KB_ID}: {update_links(records)} chunks now have a source link.")
        return 0

    rows = load_register(cfg.REGISTER_PATH)
    row = next((r for r in rows if r.get("doc_id") == KB_ID), None)
    if row is None:
        print(f"{KB_ID} is not in the register.", file=sys.stderr)
        return 1
    if not licence_allows_external_processing(row.get("licence", "")):
        print(f"{KB_ID} licence '{row.get('licence', '')}' does not allow processing.")
        return 1
    if already_ingested(row) and not args.force:
        print(f"{KB_ID} is already ingested. Pass --force to load it again.")
        return 1

    folder = records_dir()
    records, file_hash = read_records(folder)
    drafts = to_drafts(records)
    problems = check_records(records, drafts, folder)
    if problems:
        for problem in problems:
            print(f"Check failed: {problem}", file=sys.stderr)
        return 1

    texts = texts_to_embed(row, drafts)
    characters = sum(len(text) for text in texts)
    print(f"{KB_ID}: {len(drafts)} chunks from {folder}")
    print(f"  characters to embed: {characters:,} (about {characters // 4:,} tokens)")
    print(f"  longest chunk: {max(len(d.text) for d in drafts):,} characters")
    print(f"  source hash: {file_hash}")
    print(f"  chunking_mode: {row.get('chunking_mode', '')}")

    existing = existing_chunk_ids([d.chunk_id for d in drafts])
    if existing:
        print(f"  {len(existing)} chunk IDs already exist and will be replaced for {KB_ID}.")

    if not args.apply:
        print("Dry run only. Nothing embedded or written. Pass --apply to load.")
        return 0

    client = get_client()
    vectors = embed_texts(client, texts)
    document = document_record(row, SOURCE_PATH, file_hash)
    chunk_rows = chunk_records(row, drafts, vectors, source_path=SOURCE_PATH, file_hash=file_hash)
    store_pg.replace_document(document, chunk_rows)
    print(f"{KB_ID}: stored {len(chunk_rows)} chunks.")

    register_rows = load_register(cfg.REGISTER_PATH)
    for stored in register_rows:
        if stored.get("doc_id") == KB_ID:
            stored["pipeline_status"] = "indexed"
            stored["ingest_status"] = "ingested"
            stored["sha256"] = file_hash
    save_register(cfg.REGISTER_PATH, register_rows)
    print(f"Register updated: {KB_ID} marked ingested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
