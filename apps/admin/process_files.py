"""Embed cleaned documents into Postgres. Does not call OCR."""

from __future__ import annotations

import argparse
import sys

from landis import config as cfg
from landis import store_pg
from landis.embeddings import embed_texts, get_client
from landis.indexing import (
    build_manifest,
    chunk_records,
    document_record,
    drafts_for_row,
    skips_prose_chunking,
    texts_to_embed,
    write_manifest,
)
from landis.register import (
    already_ingested,
    file_sha256,
    load_register,
    ready_to_index,
    resolve_source_file,
    save_register,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Index cleaned LandIS documents")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--doc-id")
    parser.add_argument("--include-unreviewed", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Embed again even when the row is already ingested",
    )
    parser.add_argument("--full-reset", action="store_true")
    parser.add_argument("--i-know-this-wipes-data", action="store_true")
    args = parser.parse_args()
    if args.full_reset and args.doc_id:
        print("Refusing to wipe the whole index while indexing a single document.", file=sys.stderr)
        return 1
    if args.full_reset and not args.i_know_this_wipes_data:
        print("Refusing to wipe the index without --i-know-this-wipes-data.", file=sys.stderr)
        return 1

    rows = load_register(cfg.REGISTER_PATH)
    if args.doc_id:
        rows = [row for row in rows if row.get("doc_id") == args.doc_id]
        if not rows:
            print(f"{args.doc_id} is not in the register.", file=sys.stderr)
            return 1
    chosen = [
        row
        for row in rows
        if ready_to_index(row, include_unreviewed=args.include_unreviewed, force=args.force)
    ]
    skipped = [
        row["doc_id"]
        for row in rows
        if already_ingested(row)
        and not args.force
        and ready_to_index(row, include_unreviewed=args.include_unreviewed, force=True)
    ]
    if skipped:
        listed = ", ".join(skipped) if len(skipped) <= 8 else f"{len(skipped)} documents"
        print(f"Skipped already ingested: {listed}. Pass --force to embed them again.")
    if not chosen:
        if not skipped:
            print("No documents are ready to index. Accept reviewed rows, or pass --include-unreviewed.")
        return 0

    planned: list[tuple[dict, list]] = []
    for row in chosen:
        try:
            drafts = drafts_for_row(cfg.KNOWLEDGE_BASE, row)
        except FileNotFoundError as exc:
            print(exc)
            continue
        if not drafts:
            if skips_prose_chunking(row):
                print(f"{row['doc_id']}: map, not prose-chunked.")
            else:
                print(f"{row['doc_id']}: no chunks produced.")
            continue
        print(f"{row['doc_id']}: {len(drafts)} chunk(s).")
        planned.append((row, drafts))
    if args.dry_run or not planned:
        return 0

    if args.full_reset:
        removed = store_pg.clear_index()
        print(f"Cleared index ({removed[0]} chunks, {removed[1]} documents).")

    client = get_client()
    indexed_ids: list[str] = []
    chunk_count = 0
    register_rows = load_register(cfg.REGISTER_PATH)
    by_id = {row["doc_id"]: row for row in register_rows}
    for row, drafts in planned:
        path = resolve_source_file(cfg.KNOWLEDGE_BASE, row.get("file_name", ""))
        source_path = str(path) if path else row.get("file_name") or row["doc_id"]
        file_hash = file_sha256(path) if path else (row.get("sha256") or "")
        vectors = embed_texts(client, texts_to_embed(row, drafts))
        store_pg.replace_document(
            document_record(row, source_path, file_hash),
            chunk_records(row, drafts, vectors, source_path=source_path, file_hash=file_hash),
        )
        stored = by_id.get(row["doc_id"], row)
        stored["pipeline_status"] = "indexed"
        stored["ingest_status"] = "ingested"
        stored["sha256"] = file_hash
        indexed_ids.append(row["doc_id"])
        chunk_count += len(drafts)
        print(f"{row['doc_id']}: indexed.")

    save_register(cfg.REGISTER_PATH, list(by_id.values()))
    manifest = build_manifest(documents=len(indexed_ids), chunks=chunk_count, doc_ids=indexed_ids)
    write_manifest(cfg.INDEX_MANIFEST_PATH, manifest)
    print(f"Manifest written to {cfg.INDEX_MANIFEST_PATH}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
