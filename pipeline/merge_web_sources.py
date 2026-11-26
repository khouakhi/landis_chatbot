"""
Merge the staged LandIS web-source list into the corpus register and capture
the rows marked include = yes.

Run from the code folder:

    uv run --with playwright python pipeline/merge_web_sources.py

**Created:** 30-10-2026 (UK style).
"""

from __future__ import annotations

import csv
import shutil
import sys
import traceback
import urllib.error
from datetime import date
from pathlib import Path

from landis import config as cfg
from landis.register import empty_row, file_sha256, load_register, next_kb_id, save_register

from web_capture import (
    RequestPace,
    capture_url,
    download_bytes,
    pdf_name_from_response,
)

STAGING_NAME = "LandIS_web_sources_for_KB.csv"
CAPTURED_ON = date.today().isoformat()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def _backup_register(register_path: Path) -> Path:
    stamp = date.today().strftime("%Y%m%d")
    folder = register_path.parent / "_backup"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"corpus_register_{stamp}-merge.csv"
    if dest.exists():
        dest = folder / f"corpus_register_{stamp}-merge-2.csv"
    shutil.copy2(register_path, dest)
    return dest


def main() -> int:
    knowledge_base = cfg.KNOWLEDGE_BASE
    staging = knowledge_base / "05_Metadata" / "_staging" / STAGING_NAME
    if not staging.is_file():
        print(f"Staging file not found: {staging}", file=sys.stderr)
        return 1
    register_path = cfg.REGISTER_PATH
    backup = _backup_register(register_path)
    print(f"Backed up the register to {backup}")

    existing = load_register(register_path)
    known_urls = {
        (row.get("source_url") or "").strip()
        for row in existing
        if (row.get("source_url") or "").strip()
    }
    staged = _read_csv(staging)
    structured: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    chosen: list[tuple[dict[str, str], dict[str, str]]] = []
    skipped_notes = 0
    skipped_urls = 0

    for source in staged:
        source_type = (source.get("source_type") or "").strip()
        include = (source.get("include") or "").strip().lower()
        url = (source.get("source_url") or "").strip()
        if source_type == "tool_note":
            skipped_notes += 1
            continue
        if url and url in known_urls:
            skipped_urls += 1
            continue
        if include == "structured":
            structured.append(source)
            continue
        if include == "no":
            excluded.append(source)
            continue
        if include not in {"yes", "check"}:
            excluded.append({**source, "notes": source.get("notes") or f"Unrecognised include value {include!r}"})
            continue
        row = empty_row()
        row["doc_id"] = next_kb_id(existing + [item[1] for item in chosen])
        row["title"] = (source.get("title") or "").strip()
        row["doc_type"] = (source.get("doc_type") or "").strip()
        row["source_url"] = url
        row["authority_tier"] = "LandIS definitive"
        row["ingest_status"] = "not started"
        note = (source.get("notes") or "").strip()
        folder = (source.get("proposed_folder") or "").strip()
        if include == "yes":
            row["pipeline_status"] = "raw"
            row["notes"] = note
        else:
            row["pipeline_status"] = "needs_review"
            row["qa_flags"] = note
            row["metadata_note"] = (
                f"Proposed folder: {folder}. Registered for review; not captured."
            )
        chosen.append((source, row))
        print(f"{(source.get('proposed_kb_id') or '(blank)'):8} -> {row['doc_id']}  {include:5}  {row['title']}")

    fieldnames = list(staged[0].keys()) if staged else []
    if "notes" not in fieldnames:
        fieldnames.append("notes")
    _write_csv(
        knowledge_base / "03_Structured_data" / "_requests" / "structured_exports_needed.csv",
        structured,
        fieldnames,
    )
    excluded_fields = fieldnames + (["reason"] if "reason" not in fieldnames else [])
    for row in excluded:
        row["reason"] = (row.get("notes") or "").strip() or "include = no"
    _write_csv(
        knowledge_base / "06_Excluded" / "excluded_web_sources.csv",
        excluded,
        excluded_fields,
    )

    pace = RequestPace()
    downloaded: list[str] = []
    captured: list[str] = []
    failures: list[str] = []
    raw_root = knowledge_base / "01_Source_raw"

    for source, row in chosen:
        if row["pipeline_status"] != "raw":
            continue
        url = row["source_url"]
        source_type = (source.get("source_type") or "").strip()
        folder = (source.get("proposed_folder") or "").strip()
        try:
            pace.wait()
            if source_type == "pdf":
                data, disposition, content_type = download_bytes(url)
                if not data.startswith(b"%PDF") and "pdf" not in content_type.lower():
                    raise RuntimeError(f"response was not a PDF ({content_type or 'no content type'})")
                filename = pdf_name_from_response(url, disposition, f"{row['doc_id']}_download.pdf")
                relative = f"{folder}/{row['doc_id']}_{filename}"
                dest = raw_root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                row["file_name"] = relative
                row["sha256"] = file_sha256(dest)
                row["downloaded_date"] = CAPTURED_ON
                downloaded.append(f"{row['doc_id']}: {relative}")
                print(f"{row['doc_id']}: downloaded {relative}")
            else:
                dest = raw_root / "07_Portal_web_content" / row["doc_id"]
                capture_url(
                    url,
                    dest,
                    doc_id=row["doc_id"],
                    title=row["title"],
                    captured_on=CAPTURED_ON,
                )
                clean = dest / "clean.md"
                row["file_name"] = f"07_Portal_web_content/{row['doc_id']}/clean.md"
                row["sha256"] = file_sha256(clean)
                row["downloaded_date"] = CAPTURED_ON
                captured.append(f"{row['doc_id']}: {row['title']}")
                print(f"{row['doc_id']}: captured {row['title']}")
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            message = f"{row['doc_id']}: {row['title']} — {exc}"
            failures.append(message)
            row["metadata_note"] = f"Capture failed on {CAPTURED_ON}. {exc}"
            print(message, file=sys.stderr)
        except Exception as exc:  # a browser fault should not drop the other rows
            message = f"{row['doc_id']}: {row['title']} — {exc}"
            failures.append(message)
            row["metadata_note"] = f"Capture failed on {CAPTURED_ON}. {exc}"
            print(message, file=sys.stderr)
            traceback.print_exc()

    save_register(register_path, existing + [row for _source, row in chosen])
    processed = staging.parent / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    moved = processed / f"LandIS_web_sources_for_KB_{CAPTURED_ON}.csv"
    shutil.move(staging, moved)

    raw_count = sum(1 for _source, row in chosen if row["pipeline_status"] == "raw")
    review_count = sum(1 for _source, row in chosen if row["pipeline_status"] == "needs_review")
    print("")
    print(f"Skipped tool notes: {skipped_notes}")
    print(f"Skipped because the URL was already registered: {skipped_urls}")
    print(f"Added status raw: {raw_count}")
    print(f"Added status needs_review: {review_count}")
    print(f"Structured requests: {len(structured)}")
    print(f"Excluded: {len(excluded)}")
    print(f"PDFs downloaded: {len(downloaded)}")
    print(f"Pages captured: {len(captured)}")
    print(f"Failures: {len(failures)}")
    for line in failures:
        print(f"  {line}")
    print(f"Staging sheet moved to {moved}")
    print(f"Register: {register_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
