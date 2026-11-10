"""
Build Mistral OCR requests. No network calls in this module.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

from pathlib import Path

from . import config as cfg


def document_kind(path: Path) -> str:
    """``image`` or ``document`` for the OCR document block."""
    if path.suffix.lower() in cfg.IMAGE_EXTENSIONS:
        return "image"
    return "document"


def document_block(url: str, kind: str) -> dict:
    if kind == "image":
        return {"type": "image_url", "image_url": url}
    return {"type": "document_url", "document_url": url}


def ocr_body(url: str, kind: str) -> dict:
    """Request body shared by the synchronous call and each batch line."""
    return {
        "document": document_block(url, kind),
        "table_format": cfg.OCR_TABLE_FORMAT,
        "extract_header": cfg.OCR_EXTRACT_HEADER,
        "extract_footer": cfg.OCR_EXTRACT_FOOTER,
        "include_blocks": cfg.OCR_INCLUDE_BLOCKS,
        "confidence_scores_granularity": cfg.OCR_CONFIDENCE_GRANULARITY,
        "include_image_base64": cfg.OCR_INCLUDE_IMAGE_BASE64,
    }


def batch_line(custom_id: str, url: str, kind: str) -> dict:
    """One JSONL line. ``custom_id`` is the corpus register doc id."""
    return {"custom_id": custom_id, "body": ocr_body(url, kind)}


def estimate_cost_usd(pages: int, *, batch: bool) -> float:
    """List-price estimate for the run log. Not a bill."""
    rate = cfg.OCR_BATCH_USD_PER_PAGE if batch else cfg.OCR_STANDARD_USD_PER_PAGE
    return round(max(0, int(pages)) * rate, 4)


def needs_ocr(path: Path) -> bool:
    return path.suffix.lower() in cfg.OCR_EXTENSIONS


def extract_ocr_payload(record: dict) -> tuple[str, dict]:
    """
    Pull ``(custom_id, ocr payload)`` out of a batch output line.

    Batch output shapes differ by SDK version, so several layouts are accepted.
    """
    custom_id = str(record.get("custom_id") or "")
    response = record.get("response", record)
    if not isinstance(response, dict):
        return custom_id, {}
    body = response.get("body", response)
    if isinstance(body, dict) and "pages" not in body and isinstance(body.get("response"), dict):
        body = body["response"]
    if not isinstance(body, dict):
        return custom_id, {}
    return custom_id, body
