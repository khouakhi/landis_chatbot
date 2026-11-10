"""
Chat API.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from landis import config as cfg
from landis.conversation_log import fetch_recent_turns
from landis.services import ChatService, ChatSource
from landis.user_identity import metadata_from_request

router = APIRouter(prefix="/api", tags=["chat"])
chat_service = ChatService()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = None


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


class ChatResponseBody(BaseModel):
    answer: str
    sources: list[ChatSourceResponse]
    session_id: str
    suggested_questions: list[str] = Field(default_factory=list)
    error: str | None = None


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


@router.post("/chat", response_model=ChatResponseBody)
async def chat(body: ChatRequest) -> ChatResponseBody:
    session_id = (body.session_id or "").strip() or str(uuid.uuid4())
    client = metadata_from_request(session_id=session_id, client_type="api")
    history = fetch_recent_turns(session_id) if cfg.CHAT_HISTORY_ENABLED else []
    response = chat_service.chat(body.message.strip(), history=history, client=client)
    return ChatResponseBody(
        answer=response.answer,
        sources=[_source_response(source) for source in response.sources],
        session_id=session_id,
        suggested_questions=response.suggested_questions or [],
        error=response.error,
    )
