"""
Mistral Document AI OCR: upload a local file, then process it.

Synchronous ``ocr.process`` is the pilot path. The batch path writes one
JSONL line per document (``custom_id`` = doc id) and uses ``/v1/ocr``.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from .ocr_layout import copy_plain_text, count_pages, normalise_keys, write_cleaned_document
from .ocr_request import batch_line, document_kind, estimate_cost_usd, needs_ocr, ocr_body
from .register import licence_allows_external_processing, resolve_source_file
from .retry import call_with_retry

logger = logging.getLogger(__name__)


class OcrSkipped(RuntimeError):
    """A document was not sent, with a reason safe to print."""


def get_client():
    """Mistral client. Imported here so register tests do not need the SDK."""
    if not cfg.MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_API_KEY is not set. Add it to .env in the code folder.")
    from mistralai.client import Mistral

    return Mistral(api_key=cfg.MISTRAL_API_KEY)


def _as_dict(result) -> dict:
    if isinstance(result, dict):
        return _json_ready(result)
    if hasattr(result, "model_dump"):
        return _json_ready(result.model_dump(mode="json"))
    if hasattr(result, "dict"):
        return _json_ready(result.dict())
    raise TypeError("Unexpected OCR response type.")


def _json_ready(value):
    """Drop SDK sentinels so the saved JSON is plain data."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if type(item).__name__ in {"Unset", "UNSET"}:
                continue
            cleaned[str(key)] = _json_ready(item)
        return cleaned
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _signed_url(client, path: Path, *, expiry_hours: int = 24) -> str:
    with path.open("rb") as handle:
        uploaded = client.files.upload(
            file={"file_name": path.name, "content": handle},
            purpose="ocr",
        )
    file_id = getattr(uploaded, "id", None)
    if not file_id and isinstance(uploaded, dict):
        file_id = uploaded.get("id")
    if not file_id:
        raise RuntimeError(f"Mistral file upload did not return an id for {path.name}.")
    signed = client.files.get_signed_url(file_id=file_id, expiry=expiry_hours)
    url = getattr(signed, "url", None)
    if not url and isinstance(signed, dict):
        url = signed.get("url")
    if not url:
        raise RuntimeError(f"Mistral did not return a signed URL for {path.name}.")
    return str(url)


def process_path(client, path: Path) -> dict:
    """Upload one file and run synchronous OCR."""
    url = _signed_url(client, path)
    body = ocr_body(url, document_kind(path))

    def call():
        return client.ocr.process(model=cfg.OCR_MODEL, **body)

    payload = normalise_keys(_as_dict(call_with_retry(call)))
    return payload


def append_run_log(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def ocr_output_ready(cleaned_root: Path, doc_id: str) -> bool:
    return (cleaned_root / doc_id / "raw_response.json").is_file()


def prepare_row(row: dict, knowledge_base: Path, cleaned_root: Path, *, force: bool) -> Path:
    """Return the source path, or raise OcrSkipped with the reason."""
    doc_id = row.get("doc_id") or ""
    if not licence_allows_external_processing(row.get("licence", "")):
        raise OcrSkipped(f"{doc_id}: licence {row.get('licence') or '(blank)'} is not sent for OCR.")
    if (cleaned_root / doc_id / "CURATED").is_file() and not force:
        raise OcrSkipped(f"{doc_id}: folder is marked CURATED and was left unchanged.")
    path = resolve_source_file(knowledge_base, row.get("file_name", ""))
    if path is None:
        raise OcrSkipped(f"{doc_id}: file not found ({row.get('file_name') or 'no file name'}).")
    if not needs_ocr(path):
        if path.suffix.lower() in {".md", ".txt", ".html"}:
            copy_plain_text(cleaned_root, doc_id, path)
            row["pipeline_status"] = "cleaned"
            row["ingest_status"] = "cleaned"
            raise OcrSkipped(
                f"{doc_id}: {path.suffix} skipped OCR and was copied to "
                f"02_Source_cleaned/{doc_id}/."
            )
        raise OcrSkipped(f"{doc_id}: {path.suffix} does not need OCR.")
    if ocr_output_ready(cleaned_root, doc_id) and not force:
        current = (row.get("sha256") or "").strip()
        raise OcrSkipped(f"{doc_id}: OCR output already exists (hash {current[:12]}).")
    return path


def run_sync(rows: list[dict], *, force: bool = False, dry_run: bool = False) -> list[dict]:
    """OCR each row synchronously. Updates pipeline fields on the returned rows."""
    client = None if dry_run else get_client()
    updated = [dict(row) for row in rows]
    by_id = {row["doc_id"]: row for row in updated}
    for row in updated:
        doc_id = row["doc_id"]
        try:
            path = prepare_row(row, cfg.KNOWLEDGE_BASE, cfg.SOURCE_CLEANED, force=force)
        except OcrSkipped as exc:
            logger.info("%s", exc)
            print(exc)
            continue
        if dry_run:
            print(f"{doc_id}: would OCR {path.name} (synchronous).")
            continue
        assert client is not None
        payload = process_path(client, path)
        write_cleaned_document(cfg.SOURCE_CLEANED, doc_id, payload)
        pages = count_pages(payload)
        by_id[doc_id]["pipeline_status"] = "ocr_done"
        by_id[doc_id]["pages_processed"] = str(pages)
        by_id[doc_id]["ocr_model"] = str(payload.get("model") or cfg.OCR_MODEL)
        cost = estimate_cost_usd(pages, batch=False)
        append_run_log(
            cfg.OCR_LOG_PATH,
            {
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "doc_id": doc_id,
                "mode": "sync",
                "pages": pages,
                "estimated_usd": cost,
                "model": by_id[doc_id]["ocr_model"],
            },
        )
        print(f"{doc_id}: OCR saved, {pages} page(s), estimated USD {cost:.4f}.")
    return updated


def submit_batch(rows: list[dict], *, force: bool = False, dry_run: bool = False) -> str | None:
    """Upload eligible files, submit one batch job, and record its id."""
    planned: list[tuple[dict, Path]] = []
    for row in rows:
        try:
            path = prepare_row(row, cfg.KNOWLEDGE_BASE, cfg.SOURCE_CLEANED, force=force)
        except OcrSkipped as exc:
            print(exc)
            continue
        planned.append((row, path))
    if not planned:
        print("No documents to submit.")
        return None
    if dry_run:
        for row, path in planned:
            print(f"{row['doc_id']}: would add {path.name} to a batch.")
        return None

    client = get_client()
    lines = []
    for row, path in planned:
        url = _signed_url(client, path, expiry_hours=168)
        lines.append(batch_line(row["doc_id"], url, document_kind(path)))
        row["pipeline_status"] = "ocr_queued"
    raw = ("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n").encode("utf-8")
    uploaded = client.files.upload(
        file={"file_name": "landis-ocr-batch.jsonl", "content": raw},
        purpose="batch",
    )
    file_id = getattr(uploaded, "id", None)
    job = client.batch.jobs.create(
        input_files=[file_id],
        model=cfg.OCR_MODEL,
        endpoint="/v1/ocr",
        metadata={"job_type": "landis-ocr"},
    )
    job_id = str(getattr(job, "id", "") or "")
    cfg.OCR_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "job_id": job_id,
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "doc_ids": [row["doc_id"] for row, _path in planned],
        "model": cfg.OCR_MODEL,
    }
    (cfg.OCR_JOBS_DIR / f"{job_id}.json").write_text(
        json.dumps(record, indent=2),
        encoding="utf-8",
    )
    print(f"Submitted batch {job_id} with {len(lines)} document(s).")
    print("Signed URLs last up to a week. Collect the job with landis-ocr-collect.")
    return job_id


def _download_bytes(client, file_id: str) -> bytes:
    downloaded = client.files.download(file_id=file_id)
    if isinstance(downloaded, bytes):
        return downloaded
    if isinstance(downloaded, str):
        return downloaded.encode("utf-8")
    content = getattr(downloaded, "content", None)
    if isinstance(content, bytes):
        return content
    read = getattr(downloaded, "read", None)
    if callable(read):
        data = read()
        return data if isinstance(data, bytes) else bytes(data)
    raise RuntimeError("Could not read the batch output file.")


def collect_batch(job_id: str, rows: list[dict]) -> list[dict]:
    """Download a finished batch and write the cleaned-folder layout."""
    client = get_client()
    job = client.batch.jobs.get(job_id=job_id)
    status = str(getattr(job, "status", "") or "")
    if status.upper() not in {"SUCCESS", "COMPLETED", "DONE"}:
        raise RuntimeError(f"Batch {job_id} is not ready (status {status or 'unknown'}).")
    output_file = getattr(job, "output_file", None)
    if not output_file:
        raise RuntimeError(f"Batch {job_id} has no output file.")
    raw = _download_bytes(client, str(output_file))
    by_id = {row["doc_id"]: row for row in rows}
    pages_total = 0
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        custom_id, payload = _payload_from_line(json.loads(line))
        payload = normalise_keys(payload)
        if not custom_id or custom_id not in by_id:
            print(f"Skipping batch line {custom_id or '(no id)'}: not in the register.")
            continue
        if "pages" not in payload:
            print(f"{custom_id}: batch line had no pages. Left unchanged.")
            continue
        write_cleaned_document(cfg.SOURCE_CLEANED, custom_id, payload)
        pages = count_pages(payload)
        pages_total += pages
        by_id[custom_id]["pipeline_status"] = "ocr_done"
        by_id[custom_id]["pages_processed"] = str(pages)
        by_id[custom_id]["ocr_model"] = str(payload.get("model") or cfg.OCR_MODEL)
        print(f"{custom_id}: OCR saved, {pages} page(s).")
    cost = estimate_cost_usd(pages_total, batch=True)
    append_run_log(
        cfg.OCR_LOG_PATH,
        {
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "job_id": job_id,
            "mode": "batch",
            "pages": pages_total,
            "estimated_usd": cost,
            "model": cfg.OCR_MODEL,
        },
    )
    print(f"Batch {job_id}: {pages_total} page(s), estimated USD {cost:.4f}.")
    return list(by_id.values())


def _payload_from_line(record: dict) -> tuple[str, dict]:
    from .ocr_request import extract_ocr_payload

    return extract_ocr_payload(record)
