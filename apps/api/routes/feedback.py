"""
Answer feedback: thumbs up or down, with an optional reason for thumbs down.

Each rating is appended to a daily JSONL file. No IP address or user agent is stored.

**Created:** 22-11-2026 (UK style).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from landis import config as cfg
from landis.feedback import save_feedback

from .chat import Audience, FormatTag, Lang

router = APIRouter(prefix="/api", tags=["feedback"])
logger = logging.getLogger(__name__)

FeedbackReason = Literal["incorrect", "not_answered", "too_technical", "too_simple"]


class FeedbackRequest(BaseModel):
    rating: Literal["up", "down"]
    reason: FeedbackReason | None = None
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(default="", max_length=20000)
    audience: Audience = "general"
    formats: list[FormatTag] = Field(default_factory=list, max_length=5)
    lang: Lang = "en"
    cited_chunk_ids: list[str] = Field(default_factory=list, max_length=20)
    response_ms: int | None = Field(default=None, ge=0, le=3_600_000)

    @model_validator(mode="after")
    def _reason_only_for_thumbs_down(self) -> FeedbackRequest:
        if self.reason is not None and self.rating != "down":
            raise ValueError("A reason can only be given with a thumbs-down rating.")
        return self


@router.post("/feedback", status_code=201)
async def submit_feedback(body: FeedbackRequest) -> dict[str, bool]:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rating": body.rating,
        "reason": body.reason,
        "question": body.question.strip(),
        "answer": body.answer,
        "audience": body.audience,
        "formats": list(dict.fromkeys(body.formats)),
        "lang": body.lang,
        "cited_chunk_ids": [chunk_id for chunk_id in body.cited_chunk_ids if chunk_id],
        "response_ms": body.response_ms,
        "kb_version": cfg.KB_VERSION,
    }
    try:
        save_feedback(record)
    except OSError as exc:
        logger.error("Feedback could not be written: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Feedback could not be saved.") from exc
    return {"saved": True}
