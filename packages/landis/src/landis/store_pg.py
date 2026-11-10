"""PostgreSQL + pgvector access for documents, chunks, and conversations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from . import config as cfg

logger = logging.getLogger(__name__)

_conn: psycopg.Connection | None = None
_conn_url: str | None = None


class DatabaseError(RuntimeError):
    """DATABASE_URL is missing or the database cannot be reached."""


def _require_database_url() -> str:
    url = (cfg.DATABASE_URL or "").strip()
    if not url:
        raise DatabaseError(
            "DATABASE_URL is not set. Add the Render Postgres connection string to .env."
        )
    return url


def get_connection() -> psycopg.Connection:
    """Shared connection. Reconnects if DATABASE_URL changes."""
    global _conn, _conn_url
    url = _require_database_url()
    if _conn is None or _conn.closed or _conn_url != url:
        if _conn is not None and not _conn.closed:
            _conn.close()
        _conn = psycopg.connect(url, row_factory=dict_row)
        try:
            register_vector(_conn)
        except psycopg.ProgrammingError:
            _conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            _conn.commit()
            register_vector(_conn)
        _conn_url = url
    return _conn


def ping_database() -> None:
    get_connection().execute("SELECT 1")


def run_sql_file(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    statements = [part.strip() for part in text.split(";") if part.strip()]
    conn = get_connection()
    for statement in statements:
        conn.execute(statement)
    conn.commit()


def init_schema() -> None:
    if not cfg.SQL_DIR.is_dir():
        raise FileNotFoundError(f"SQL directory not found: {cfg.SQL_DIR}")
    for path in sorted(cfg.SQL_DIR.glob("*.sql")):
        run_sql_file(path)


def count_chunks() -> int:
    row = get_connection().execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
    return int(row["n"]) if row else 0


def clear_index() -> tuple[int, int]:
    """Delete chunks and documents. Returns ``(chunks, documents)``."""
    conn = get_connection()
    chunks = conn.execute("DELETE FROM chunks").rowcount
    documents = conn.execute("DELETE FROM documents").rowcount
    conn.commit()
    return int(chunks or 0), int(documents or 0)


def replace_document(document: dict[str, Any], chunk_rows: list[dict[str, Any]]) -> None:
    """Replace one document and its chunks in a single transaction."""
    conn = get_connection()
    now = datetime.now(timezone.utc)
    conn.execute("DELETE FROM chunks WHERE doc_id = %s", (document["doc_id"],))
    conn.execute(
        """
        INSERT INTO documents (
            doc_id, source_path, file_hash, title, public_url, is_public,
            publication_year, document_type, authority_tier, licence,
            superseded, place, spatial_scale, chunking_mode, pipeline_status,
            last_processed
        ) VALUES (
            %(doc_id)s, %(source_path)s, %(file_hash)s, %(title)s, %(public_url)s,
            %(is_public)s, %(publication_year)s, %(document_type)s, %(authority_tier)s,
            %(licence)s, %(superseded)s, %(place)s, %(spatial_scale)s,
            %(chunking_mode)s, %(pipeline_status)s, %(last_processed)s
        )
        ON CONFLICT (doc_id) DO UPDATE SET
            source_path = EXCLUDED.source_path,
            file_hash = EXCLUDED.file_hash,
            title = EXCLUDED.title,
            public_url = EXCLUDED.public_url,
            is_public = EXCLUDED.is_public,
            publication_year = EXCLUDED.publication_year,
            document_type = EXCLUDED.document_type,
            authority_tier = EXCLUDED.authority_tier,
            licence = EXCLUDED.licence,
            superseded = EXCLUDED.superseded,
            place = EXCLUDED.place,
            spatial_scale = EXCLUDED.spatial_scale,
            chunking_mode = EXCLUDED.chunking_mode,
            pipeline_status = EXCLUDED.pipeline_status,
            last_processed = EXCLUDED.last_processed
        """,
        {**document, "last_processed": now},
    )
    if chunk_rows:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (
                    chunk_id, doc_id, source_path, source_hash, chunk_index, section,
                    text, table_html, entity_codes, page_start, page_end,
                    printed_page_start, printed_page_end, has_table, embedding
                ) VALUES (
                    %(chunk_id)s, %(doc_id)s, %(source_path)s, %(source_hash)s,
                    %(chunk_index)s, %(section)s, %(text)s, %(table_html)s,
                    %(entity_codes)s, %(page_start)s, %(page_end)s,
                    %(printed_page_start)s, %(printed_page_end)s, %(has_table)s,
                    %(embedding)s
                )
                """,
                chunk_rows,
            )
    conn.commit()


def _vector(value) -> np.ndarray:
    """Float32 vector. pgvector returns its own Vector type, which NumPy cannot cast."""
    if hasattr(value, "to_numpy"):
        value = value.to_numpy()
    return np.asarray(value, dtype="float32").reshape(-1)


def search_similar(query_vector: np.ndarray, fetch_k: int) -> list[dict[str, Any]]:
    """Cosine search. Each hit includes the embedding for MMR."""
    query = _vector(query_vector)
    cur = get_connection().execute(
        """
        SELECT
            c.chunk_id,
            c.entity_codes,
            d.authority_tier,
            d.superseded,
            1 - (c.embedding <=> %s::vector) AS similarity,
            c.embedding
        FROM chunks AS c
        JOIN documents AS d ON d.doc_id = c.doc_id
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
        """,
        (query.tolist(), query.tolist(), int(fetch_k)),
    )
    hits = []
    for row in cur.fetchall():
        hits.append(
            {
                "chunk_id": str(row["chunk_id"]),
                "entity_codes": str(row["entity_codes"] or ""),
                "authority_tier": str(row["authority_tier"] or ""),
                "superseded": bool(row["superseded"]),
                "similarity": float(row["similarity"]),
                "embedding": _vector(row["embedding"]),
            }
        )
    return hits


def search_keywords(query: str, fetch_k: int) -> list[dict[str, Any]]:
    """Postgres full-text search over passage text, section, and entity codes."""
    cur = get_connection().execute(
        """
        SELECT
            c.chunk_id,
            c.entity_codes,
            d.authority_tier,
            d.superseded,
            ts_rank_cd(c.search_vector, plainto_tsquery('english', %s)) AS rank
        FROM chunks AS c
        JOIN documents AS d ON d.doc_id = c.doc_id
        WHERE c.search_vector @@ plainto_tsquery('english', %s)
        ORDER BY rank DESC
        LIMIT %s
        """,
        (query, query, int(fetch_k)),
    )
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "entity_codes": str(row["entity_codes"] or ""),
            "authority_tier": str(row["authority_tier"] or ""),
            "superseded": bool(row["superseded"]),
            "rank": float(row["rank"]),
        }
        for row in cur.fetchall()
    ]


def fetch_embeddings(chunk_ids: list[str]) -> dict[str, np.ndarray]:
    if not chunk_ids:
        return {}
    cur = get_connection().execute(
        "SELECT chunk_id, embedding FROM chunks WHERE chunk_id = ANY(%s)",
        (chunk_ids,),
    )
    return {str(row["chunk_id"]): _vector(row["embedding"]) for row in cur.fetchall()}


def fetch_chunks(chunk_ids: list[str]) -> list[dict[str, Any]]:
    if not chunk_ids:
        return []
    cur = get_connection().execute(
        """
        SELECT
            c.chunk_id, c.doc_id, c.source_path, c.section, c.text, c.entity_codes,
            c.page_start, c.page_end, c.printed_page_start, c.printed_page_end,
            c.has_table,
            d.title, d.public_url, d.is_public, d.authority_tier, d.superseded,
            d.publication_year, d.licence
        FROM chunks AS c
        JOIN documents AS d ON d.doc_id = c.doc_id
        WHERE c.chunk_id = ANY(%s)
        """,
        (chunk_ids,),
    )
    by_id = {str(row["chunk_id"]): dict(row) for row in cur.fetchall()}
    return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]


def insert_conversation(doc: dict[str, Any]) -> int:
    row = get_connection().execute(
        """
        INSERT INTO landis_conversations (
            created_at, thread_id, session_id, visitor_fingerprint, client_type,
            question, answer, error, cited_chunk_ids, cited_sources_count,
            rag_top_k, chat_model, embed_model, kb_version
        ) VALUES (
            %(created_at)s, %(thread_id)s, %(session_id)s, %(visitor_fingerprint)s,
            %(client_type)s, %(question)s, %(answer)s, %(error)s, %(cited_chunk_ids)s,
            %(cited_sources_count)s, %(rag_top_k)s, %(chat_model)s, %(embed_model)s,
            %(kb_version)s
        )
        RETURNING id
        """,
        doc,
    ).fetchone()
    get_connection().commit()
    return int(row["id"]) if row else 0


def delete_conversations_older_than(days: int) -> int:
    if days <= 0:
        return 0
    cur = get_connection().execute(
        """
        DELETE FROM landis_conversations
        WHERE created_at < NOW() - (%(days)s * INTERVAL '1 day')
        """,
        {"days": days},
    )
    deleted = int(cur.rowcount or 0)
    get_connection().commit()
    return deleted


def fetch_recent_turns(thread_id: str, limit: int) -> list[dict[str, str]]:
    cur = get_connection().execute(
        """
        SELECT question, answer
        FROM landis_conversations
        WHERE thread_id = %s
          AND answer IS NOT NULL
          AND TRIM(answer) <> ''
          AND (error IS NULL OR TRIM(error) = '')
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (thread_id, limit),
    )
    rows = list(cur.fetchall())
    rows.reverse()
    return [
        {"question": str(row["question"]), "answer": str(row["answer"])}
        for row in rows
        if row.get("question") and row.get("answer")
    ]
