"""Pre-fill register metadata from OCR pages and write QA flags."""

from __future__ import annotations

import argparse

from landis import config as cfg
from landis.metadata import apply_prefill
from landis.ocr_layout import load_page_metadata
from landis.qa import review_document
from landis.register import load_register, save_register, select_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-fill corpus register metadata from OCR")
    parser.add_argument("--doc-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rows = load_register(cfg.REGISTER_PATH)
    target_ids = {row["doc_id"] for row in select_rows(rows, doc_id=args.doc_id)}
    if args.doc_id and not target_ids:
        print(f"{args.doc_id} is not in the register.")
        return 1
    changed = 0
    for row in rows:
        if row["doc_id"] not in target_ids:
            continue
        doc_dir = cfg.SOURCE_CLEANED / row["doc_id"]
        if not (doc_dir / "raw_response.json").is_file():
            continue
        pages = load_page_metadata(doc_dir)
        updated = apply_prefill(row, pages)
        flags = review_document(
            pages,
            low_confidence=cfg.QA_LOW_CONFIDENCE,
            max_low_words=cfg.QA_MAX_LOW_WORDS,
            near_empty_words=cfg.QA_NEAR_EMPTY_WORDS,
            register_year=updated.get("publication_year") or "",
        )
        updated["qa_flags"] = " | ".join(flags)
        if flags and updated.get("pipeline_status") in {"ocr_done", "ocr_queued", "not_started"}:
            updated["pipeline_status"] = "needs_review"
        row.clear()
        row.update(updated)
        changed += 1
        state = "needs review" if flags else "no QA flags"
        print(f"{row['doc_id']}: metadata updated ({state}).")
    if args.dry_run:
        print(f"Dry run: {changed} document(s) not written.")
        return 0
    save_register(cfg.REGISTER_PATH, rows)
    print("Subject classes were left blank.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
