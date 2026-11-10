"""
Log questions and answers. Failures are logged and do not break the chat.

No IP address or user-agent is written.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import config as cfg
from . import store_pg
from .chat_history import ChatTurn
from .user_identity import ClientMetadata, coerce_client_metadata

logger = logging.getLogger(__name__)


def fetch_recent_turns(thread_id: str, limit: int | None = None) -> list[ChatTurn]:
    if not cfg.LOG_CONVERSATIONS or not thread_id or thread_id == "anonymous":
        return []
    turn_limit = cfg.CHAT_HISTORY_TURNS if limit is None else limit
    if turn_limit <= 0:
        return []
    try:
        rows = store_pg.fetch_recent_turns(thread_id, turn_limit)
    except Exception as exc:
        logger.warning("Could not load conversation history: %s", exc)
        return []
    return [ChatTurn(question=row["question"], answer=row["answer"]) for row in rows]


def log_interaction(
    *,
    question: str,
    answer: str | None = None,
    error: str | None = None,
    cited_chunk_ids: list[str] | None = None,
    client: ClientMetadata | dict[str, Any] | None = None,
) -> None:
    if not cfg.LOG_CONVERSATIONS or not question.strip():
        return
    meta = coerce_client_metadata(client)
    doc = {
        "created_at": datetime.now(timezone.utc),
        "thread_id": meta.thread_id,
        "session_id": meta.session_id,
        "visitor_fingerprint": meta.visitor_fingerprint,
        "client_type": meta.client_type,
        "question": question,
        "answer": answer,
        "error": error,
        "cited_chunk_ids": ",".join(cited_chunk_ids or []),
        "cited_sources_count": len(cited_chunk_ids or []),
        "rag_top_k": cfg.RAG_TOP_K,
        "chat_model": cfg.MISTRAL_CHAT_MODEL,
        "embed_model": cfg.MISTRAL_EMBED_MODEL,
        "kb_version": cfg.KB_VERSION,
    }
    try:
        store_pg.insert_conversation(doc)
    except store_pg.DatabaseError as exc:
        logger.warning("Conversation was not stored: %s", exc)
    except Exception as exc:
        logger.error("Failed to log conversation: %s", exc, exc_info=True)


def purge_expired_conversations(retention_days: int | None = None) -> int:
    days = cfg.CONVERSATION_RETENTION_DAYS if retention_days is None else max(0, int(retention_days))
    if days <= 0:
        return 0
    try:
        return store_pg.delete_conversations_older_than(days)
    except store_pg.DatabaseError as exc:
        logger.warning("Conversation retention purge skipped: %s", exc)
        return 0
    except Exception as exc:
        logger.error("Failed to purge conversations: %s", exc, exc_info=True)
        return 0
