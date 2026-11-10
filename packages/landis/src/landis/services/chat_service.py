"""Chat orchestration: retrieve, cite, and log."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .. import config as cfg
from ..chat_history import ChatTurn
from ..citations import sources_cited_in_answer, split_suggested_questions
from ..conversation_log import log_interaction
from ..markdown_tables import normalise_markdown_tables
from ..rag import SourceRef, answer_question
from ..retry import quota_message
from ..store_pg import DatabaseError
from ..user_identity import ClientMetadata


@dataclass
class ChatSource:
    label: int
    chunk_id: str
    doc_id: str
    source_path: str
    filename: str
    section: str
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


@dataclass
class ChatResponse:
    answer: str
    sources: list[ChatSource]
    top_k: int
    suggested_questions: list[str] | None = None
    error: str | None = None


def _to_source(source: SourceRef) -> ChatSource:
    path = source.source_path or ""
    return ChatSource(
        label=source.label,
        chunk_id=source.chunk_id,
        doc_id=source.doc_id,
        source_path=path,
        filename=os.path.basename(path) if path else source.doc_id,
        section=source.section,
        location_type="page",
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


class ChatService:
    """One retrieval and one completion per user message."""

    def chat(
        self,
        message: str,
        *,
        history: list[ChatTurn] | None = None,
        client: ClientMetadata | None = None,
        top_k: int | None = None,
    ) -> ChatResponse:
        text = (message or "").strip()
        k = int(top_k or cfg.RAG_TOP_K)
        if not text:
            return ChatResponse(answer="", sources=[], top_k=k)
        try:
            result = answer_question(text, top_k=k, history=history)
        except (FileNotFoundError, RuntimeError, DatabaseError) as exc:
            message_text = quota_message(exc) or str(exc)
            log_interaction(question=text, answer=None, error=message_text, client=client)
            return ChatResponse(answer=message_text, sources=[], top_k=k, error=message_text)
        except Exception as exc:
            message_text = quota_message(exc) or f"An unexpected error occurred: {exc}"
            log_interaction(question=text, answer=None, error=message_text, client=client)
            return ChatResponse(answer=message_text, sources=[], top_k=k, error=message_text)

        answer, suggested = split_suggested_questions(result.answer)
        answer = normalise_markdown_tables(answer)
        cited = sources_cited_in_answer(answer, result.sources)
        sources = [_to_source(source) for source in cited]
        log_interaction(
            question=text,
            answer=answer,
            cited_chunk_ids=[source.chunk_id for source in sources],
            client=client,
        )
        return ChatResponse(
            answer=answer,
            sources=sources,
            top_k=result.top_k,
            suggested_questions=suggested,
        )
