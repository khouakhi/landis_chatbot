"""
Chat API.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from landis import config as cfg
from landis import daily_limit
from landis.conversation_log import fetch_recent_turns
from landis.services import ChatService, ChatSource
from landis.user_identity import metadata_from_request

from .visitor import counter_unavailable, visitor_token

router = APIRouter(prefix="/api", tags=["chat"])
chat_service = ChatService()

Audience = Literal["general", "manager", "specialist"]
FormatTag = Literal["short", "bullets", "table", "steps", "compare"]
Lang = Literal["en", "cy"]


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None
    audience: Audience = "general"
    formats: list[FormatTag] = Field(default_factory=list, max_length=5)
    lang: Lang = "en"


class ChatSourceResponse(BaseModel):
    label: int
    chunk_id: str
    doc_id: str
    filename: str
    source_path: str
    section: str = ""
    location_type: str
    location_start: int
    location_end: int
    preview: str
    title: str | None = None
    public_url: str | None = None
    is_public: bool = False
    authority_tier: str = ""
    superseded: bool = False
    publication_year: int | None = None


class UsageBody(BaseModel):
    used: int
    limit: int
    remaining: int


class ChatResponseBody(BaseModel):
    answer: str
    sources: list[ChatSourceResponse]
    session_id: str
    suggested_questions: list[str] = Field(default_factory=list)
    error: str | None = None
    usage: UsageBody


def _source_response(source: ChatSource) -> ChatSourceResponse:
    return ChatSourceResponse(
        label=source.label,
        chunk_id=source.chunk_id,
        doc_id=source.doc_id,
        filename=source.filename,
        source_path=source.source_path,
        section=source.section,
        location_type=source.location_type,
        location_start=source.location_start,
        location_end=source.location_end,
        preview=source.preview,
        title=source.title,
        public_url=source.public_url,
        is_public=source.is_public,
        authority_tier=source.authority_tier,
        superseded=source.superseded,
        publication_year=source.publication_year,
    )


def _limit_reached_detail(usage: daily_limit.Usage) -> dict:
    """Friendly English fallback. The web page shows its own translated message."""
    return {
        "code": "daily_limit",
        "message": (
            f"You have reached today's limit of {usage.limit} questions. "
            "Please come back tomorrow."
        ),
        **usage.as_dict(),
    }


@router.get("/usage", response_model=UsageBody)
async def usage(request: Request, response: Response) -> UsageBody:
    token = visitor_token(request, response)
    try:
        current = daily_limit.current_usage(token)
    except Exception as exc:
        raise counter_unavailable(exc) from exc
    return UsageBody(**current.as_dict())


@router.post("/chat", response_model=ChatResponseBody)
async def chat(body: ChatRequest, request: Request, response: Response) -> ChatResponseBody:
    text = body.message.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Enter a question.")

    token = visitor_token(request, response)
    try:
        counted = daily_limit.consume_question(token)
    except Exception as exc:
        raise counter_unavailable(exc) from exc
    if counted is None:
        # At the limit: do not call retrieval or the model.
        raise HTTPException(
            status_code=429,
            detail=_limit_reached_detail(daily_limit.current_usage(token)),
        )

    session_id = (body.session_id or "").strip() or str(uuid.uuid4())
    client = metadata_from_request(session_id=session_id, client_type="api")
    history = fetch_recent_turns(session_id) if cfg.CHAT_HISTORY_ENABLED else []
    result = chat_service.chat(
        text,
        history=history,
        client=client,
        audience=body.audience,
        formats=list(dict.fromkeys(body.formats)),
        lang=body.lang,
    )
    return ChatResponseBody(
        answer=result.answer,
        sources=[_source_response(source) for source in result.sources],
        session_id=session_id,
        suggested_questions=result.suggested_questions or [],
        error=result.error,
        usage=UsageBody(**counted.as_dict()),
    )
