"""Corpus register commands: init, validate, scan, accept, workbook, apply."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime

from landis import config as cfg
from landis.indexing import has_index_input
from landis.register import (
    licence_allows_external_processing,
    load_register,
    refresh_hashes,
    save_register,
    scan_knowledge_base,
    validate_row,
    worked_example_rows,
)
from landis.register_workbook import apply_human_fields, read_workbook_edits, write_workbook


def _extensions() -> tuple[str, ...]:
    return cfg.OCR_EXTENSIONS + cfg.TEXT_EXTENSIONS


def _print_problems(rows: list[dict[str, str]]) -> int:
    problems: list[str] = []
    for row in rows:
        problems.extend(validate_row(row))
    for problem in problems:
        print(problem, file=sys.stderr)
    return len(problems)


def _print_new_rows(
    before_ids: set[str],
    rows: list[dict[str, str]],
    report: list[str],
) -> None:
    unsorted: list[str] = []
    for row in rows:
        if row.get("doc_id") in before_ids:
            continue
        name = row.get("file_name") or ""
        if name.startswith("00_Inbox/") or not (row.get("doc_type") or "").strip():
            unsorted.append(f"  {row.get('doc_id')}: {name}")
        else:
            print(f"{row.get('doc_id')}: {name} ({row.get('doc_type')})")
    if unsorted:
        print("Left unsorted:")
        for line in unsorted:
            print(line)
    for line in report:
        print(line)


def _init() -> int:
    for name in cfg.KNOWLEDGE_FOLDERS:
        (cfg.KNOWLEDGE_BASE / name).mkdir(parents=True, exist_ok=True)
    if cfg.REGISTER_PATH.is_file():
        rows = load_register(cfg.REGISTER_PATH)
        print(f"Register already exists ({len(rows)} row(s)). Scanning for new files.")
    else:
        rows = worked_example_rows()
        print(f"Created a register with {len(rows)} worked examples from the October 2026 workbook.")
    before_ids = {row.get("doc_id", "") for row in rows}
    report: list[str] = []
    rows = scan_knowledge_base(cfg.KNOWLEDGE_BASE, rows, extensions=_extensions(), report=report)
    _print_new_rows(before_ids, rows, report)
    rows = refresh_hashes(cfg.KNOWLEDGE_BASE, rows)
    save_register(cfg.REGISTER_PATH, rows)
    print(f"Wrote {cfg.REGISTER_PATH} ({len(rows)} row(s)).")
    print("Set licence to 'open' or 'LandIS licence' before OCR. Other values are not sent.")
    _print_problems(rows)
    return 0


def _accept(doc_id: str, override: bool) -> int:
    rows = load_register(cfg.REGISTER_PATH)
    found = False
    for row in rows:
        if row.get("doc_id") != doc_id:
            continue
        found = True
        if not licence_allows_external_processing(row.get("licence", "")):
            print(f"{doc_id}: licence does not allow indexing.", file=sys.stderr)
            return 1
        if (row.get("qa_flags") or "").strip() and not override:
            print(f"{doc_id}: QA flags are set. Review them, then accept with --override.", file=sys.stderr)
            print(row["qa_flags"], file=sys.stderr)
            return 1
        if not has_index_input(cfg.KNOWLEDGE_BASE, row):
            print(f"{doc_id}: no OCR output yet.", file=sys.stderr)
            return 1
        row["pipeline_status"] = "cleaned"
        row["ingest_status"] = "cleaned"
        print(f"{doc_id}: marked cleaned. It can be indexed.")
    if not found:
        print(f"{doc_id} is not in the register.", file=sys.stderr)
        return 1
    save_register(cfg.REGISTER_PATH, rows)
    return 0


def _workbook() -> int:
    if not cfg.REGISTER_PATH.is_file():
        print(f"No register at {cfg.REGISTER_PATH}.", file=sys.stderr)
        return 1
    rows = load_register(cfg.REGISTER_PATH)
    write_workbook(cfg.REGISTER_WORKBOOK_PATH, rows)
    print(f"Wrote {cfg.REGISTER_WORKBOOK_PATH} ({len(rows)} row(s)).")
    print("Edit the blue text, save the workbook, then run: uv run landis-register apply")
    print("Rebuilding replaces this file. Apply any edits first.")
    return 0


def _apply() -> int:
    if not cfg.REGISTER_WORKBOOK_PATH.is_file():
        print(f"No workbook at {cfg.REGISTER_WORKBOOK_PATH}.", file=sys.stderr)
        print("Run: uv run landis-register workbook", file=sys.stderr)
        return 1
    if not cfg.REGISTER_PATH.is_file():
        print(f"No register at {cfg.REGISTER_PATH}.", file=sys.stderr)
        return 1
    try:
        edited = read_workbook_edits(cfg.REGISTER_WORKBOOK_PATH)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    current = load_register(cfg.REGISTER_PATH)
    report = apply_human_fields(current, edited)
    for problem in report.problems:
        print(problem, file=sys.stderr)
    if report.problems:
        print("The CSV was left unchanged.", file=sys.stderr)
        return 1
    if report.absent_from_csv:
        listed = ", ".join(report.absent_from_csv[:8])
        print(f"Workbook rows with no CSV row were left out: {listed}.")
        print("New documents are added by: uv run landis-register scan")
    if report.absent_from_workbook:
        print(
            f"{len(report.absent_from_workbook)} CSV row(s) have no workbook row, "
            "so they were left as they are."
        )
    if not report.updated:
        print("No descriptive fields changed.")
        return 0
    backup_dir = cfg.METADATA_DIR / "_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    backup = backup_dir / f"corpus_register_{stamp}-before-workbook-apply.csv"
    shutil.copy2(cfg.REGISTER_PATH, backup)
    save_register(cfg.REGISTER_PATH, report.rows)
    shown = ", ".join(report.updated[:8])
    extra = f" and {len(report.updated) - 8} more" if len(report.updated) > 8 else ""
    print(f"Updated {len(report.updated)} row(s): {shown}{extra}.")
    print(f"Backup: {backup}")
    print(f"Wrote {cfg.REGISTER_PATH}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LandIS corpus register")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create folders, seed examples, and scan the knowledge base")
    sub.add_parser("validate", help="Check controlled values")
    sub.add_parser("scan", help="Add new files, place inbox files by title, and refresh hashes")
    accept = sub.add_parser("accept", help="Mark one document cleaned after review")
    accept.add_argument("doc_id")
    accept.add_argument("--override", action="store_true", help="Accept even if QA flags are set")
    sub.add_parser("workbook", help="Build the Excel form from the CSV")
    sub.add_parser("apply", help="Copy descriptive columns from the Excel form into the CSV")
    args = parser.parse_args()
    if args.command == "init":
        return _init()
    if args.command == "validate":
        rows = load_register(cfg.REGISTER_PATH)
        count = _print_problems(rows)
        print(f"{len(rows)} row(s), {count} problem(s).")
        return 1 if count else 0
    if args.command == "scan":
        rows = load_register(cfg.REGISTER_PATH)
        before_ids = {row.get("doc_id", "") for row in rows}
        report: list[str] = []
        rows = scan_knowledge_base(
            cfg.KNOWLEDGE_BASE, rows, extensions=_extensions(), report=report
        )
        _print_new_rows(before_ids, rows, report)
        rows = refresh_hashes(cfg.KNOWLEDGE_BASE, rows)
        save_register(cfg.REGISTER_PATH, rows)
        print(f"Wrote {cfg.REGISTER_PATH} ({len(rows)} row(s)).")
        return 0
    if args.command == "workbook":
        return _workbook()
    if args.command == "apply":
        return _apply()
    return _accept(args.doc_id, args.override)


if __name__ == "__main__":
    raise SystemExit(main())
