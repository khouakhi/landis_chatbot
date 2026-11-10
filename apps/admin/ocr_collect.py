"""Download a finished Mistral OCR batch into the cleaned folder."""

from __future__ import annotations

import argparse
import json

from landis import config as cfg
from landis.ocr import collect_batch
from landis.register import load_register, save_register


def _latest_job_id() -> str:
    if not cfg.OCR_JOBS_DIR.is_dir():
        return ""
    jobs = sorted(cfg.OCR_JOBS_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not jobs:
        return ""
    payload = json.loads(jobs[-1].read_text(encoding="utf-8"))
    return str(payload.get("job_id") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a Mistral OCR batch")
    parser.add_argument("--job-id", help="Batch job id. Defaults to the latest submitted job.")
    args = parser.parse_args()
    job_id = (args.job_id or "").strip() or _latest_job_id()
    if not job_id:
        print("No job id. Pass --job-id or submit a batch first.")
        return 1
    rows = load_register(cfg.REGISTER_PATH)
    updated = collect_batch(job_id, rows)
    save_register(cfg.REGISTER_PATH, updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
