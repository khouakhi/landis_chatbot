"""Submit documents to Mistral OCR (synchronous pilot, or one batch job)."""

from __future__ import annotations

import argparse

from landis import config as cfg
from landis.ocr import run_sync, submit_batch
from landis.register import load_register, save_register, select_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="OCR LandIS documents with Mistral")
    parser.add_argument("--batch", action="store_true", help="Submit one Batch API job (half price)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="OCR again even if output exists")
    parser.add_argument("--doc-id", help="Only this register row")
    args = parser.parse_args()

    rows = load_register(cfg.REGISTER_PATH)
    if not rows:
        print(f"No register at {cfg.REGISTER_PATH}. Run: uv run landis-register init")
        return 1
    target = select_rows(rows, doc_id=args.doc_id)
    if args.doc_id and not target:
        print(f"{args.doc_id} is not in the register.")
        return 1
    if args.batch:
        submit_batch(target, force=args.force, dry_run=args.dry_run)
        if not args.dry_run:
            save_register(cfg.REGISTER_PATH, rows)
        return 0
    changed = run_sync(target, force=args.force, dry_run=args.dry_run)
    if not args.dry_run:
        by_id = {row["doc_id"]: row for row in changed}
        save_register(
            cfg.REGISTER_PATH,
            [by_id.get(row["doc_id"], row) for row in rows],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
