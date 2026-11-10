"""
Retrieval and answering.

Dense search and Postgres full-text search are fused with reciprocal rank
fusion, then MMR picks a diverse set. One Mistral chat call writes the answer.
There is no tool loop.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from . import config as cfg
from . import embeddings as emb
from . import store_pg
from .chat_history import ChatTurn

_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)
_GRID = re.compile(r"\b[A-Z]{2}\s?\d{4}\s?\d{4}\b", re.IGNORECASE)
_REGULATORY = re.compile(
    r"\b(planning permission|environmental permit|legally required|am I allowed|must I)\b",
    re.IGNORECASE,
)
_FOLLOWUP = frozenset(
    {
        "that",
        "those",
        "this",
        "these",
        "them",
        "they",
        "it",
        "its",
        "above",
        "mentioned",
        "same",
    }
)


def system_prompt() -> str:
    portal = cfg.PORTAL_MAP_URL
    return (
        "You are the LandIS assistant for the National Soil Data Portal for England and Wales. "
        "You answer questions about soil data, soil classification, soil survey reports, "
        "and how to use the portal. "
        "Answer only using the numbered context excerpts. If the context does not contain "
        "the answer, say that the corpus does not cover it. "
        "Cite the excerpts you actually use, with markers such as [1] or [2, 3] next to "
        "the sentence they support. In the same sentence, name the document title, the "
        "section, and the page when the context gives them. "
        "Prefer LandIS definitive sources. If a source is marked superseded, say so and "
        "give its publication year. "
        "Do not give site-specific advice about what soil is at a place, a postcode, or a "
        f"grid reference. Point the user to the portal map instead: {portal}. "
        "Do not interpret regulations, planning rules, or what a person is legally required to do. "
        "The source corpus is in English. Answer in the language the user wrote in, "
        "and use UK English spelling when the user writes in English. "
        "Use Markdown: one ### heading, then bold **Section title:** lines, then bullets. "
        "Do not use heading levels deeper than ###. "
        "When a table helps, use GitHub-flavoured Markdown with each row on its own line "
        "and a separator row. Never put a whole table on one line. "
        "After the answer, append exactly three follow-up questions inside this block "
        "and do not put citations inside it:\n"
        "<<SUGGESTED>>\n"
        "- First follow-up question?\n"
        "- Second follow-up question?\n"
        "- Third follow-up question?\n"
        "<<END>>"
    )


@dataclass
class SourceRef:
    label: int
    chunk_id: str
    doc_id: str
    source_path: str
    title: str | None
    section: str
    location_start: int
    location_end: int
    preview: str
    public_url: str | None = None
    is_public: bool = False
    authority_tier: str = ""
    superseded: bool = False
    publication_year: int | None = None


@dataclass
class RAGResult:
    answer: str
    sources: list[SourceRef]
    top_k: int


def looks_like_point_location(question: str) -> bool:
    """True for a postcode, an eight-figure grid, or a 'what soil is here' question."""
    text = question or ""
    if _POSTCODE.search(text) or _GRID.search(text):
        return True
    lowered = text.lower()
    return "what soil" in lowered and any(
        token in lowered for token in ("postcode", "grid reference", "this field", "my garden", "where i am")
    )


def looks_like_regulatory(question: str) -> bool:
    return bool(_REGULATORY.search(question or ""))


def reciprocal_rank_fusion(rankings: list[list[str]], *, k: int = 60) -> dict[str, float]:
    """Fuse ranked id lists. Rank 0 is the best hit in each list."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores


def apply_rank_boosts(
    scores: dict[str, float],
    meta: dict[str, dict],
    question: str,
) -> dict[str, float]:
    """Prefer definitive sources, and boost a series code named in the question."""
    question_lower = question.lower()
    boosted: dict[str, float] = {}
    for chunk_id, score in scores.items():
        info = meta.get(chunk_id, {})
        value = score
        if info.get("authority_tier") == "LandIS definitive":
            value *= 1.1
        if info.get("superseded"):
            value *= 0.9
        for code in str(info.get("entity_codes") or "").split(";"):
            name = code.split(":", 1)[-1].strip().lower()
            if name and name in question_lower:
                value += 0.15
                break
        boosted[chunk_id] = value
    return boosted


def _mmr_indices(sim_q: np.ndarray, vectors: np.ndarray, k: int, lam: float) -> list[int]:
    count = int(vectors.shape[0])
    if count <= k:
        return list(range(count))
    selected = [int(np.argmax(sim_q))]
    remaining = set(range(count)) - set(selected)
    block = vectors @ vectors.T
    while len(selected) < k and remaining:
        best_index = -1
        best_score = -np.inf
        for index in remaining:
            redundancy = max(float(block[index, chosen]) for chosen in selected)
            score = lam * float(sim_q[index]) - (1.0 - lam) * redundancy
            if score > best_score:
                best_score = score
                best_index = int(index)
        selected.append(best_index)
        remaining.remove(best_index)
    return selected


def _is_follow_up(question: str) -> bool:
    tokens = {token.strip(".,?!") for token in question.lower().split()}
    return bool(tokens & _FOLLOWUP) or len(question.split()) < 10


def retrieval_query(user_message: str, history: list[ChatTurn] | None) -> str:
    if not cfg.CHAT_HISTORY_ENABLED or not cfg.CHAT_HISTORY_EXPAND_RETRIEVAL or not history:
        return user_message
    if not _is_follow_up(user_message):
        return user_message
    prior = [turn.question.strip() for turn in history if turn.question.strip()]
    combined = " ".join(prior + [user_message.strip()])
    limit = cfg.CHAT_HISTORY_RETRIEVAL_MAX_CHARS
    if len(combined) > limit:
        combined = combined[-limit:]
    return combined


def _assistant_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        text = getattr(part, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


def retrieve(client, question: str, top_k: int) -> tuple[list[SourceRef], str]:
    if store_pg.count_chunks() == 0:
        raise FileNotFoundError(
            "No indexed passages yet. OCR the corpus, check the register, "
            "then run `uv run landis-process`."
        )
    query_vector = emb.embed_query(client, question)
    fetch_k = top_k
    if cfg.RAG_MMR_ENABLED and top_k >= 2:
        fetch_k = min(max(top_k * cfg.RAG_MMR_FETCH_MULT, top_k + 1), cfg.RAG_MMR_FETCH_CAP)

    vector_hits = store_pg.search_similar(query_vector, fetch_k)
    keyword_hits = store_pg.search_keywords(question, fetch_k)
    meta: dict[str, dict] = {}
    embeddings: dict[str, np.ndarray] = {}
    for hit in vector_hits:
        meta[hit["chunk_id"]] = hit
        embeddings[hit["chunk_id"]] = hit["embedding"]
    for hit in keyword_hits:
        meta.setdefault(hit["chunk_id"], hit)

    fused = reciprocal_rank_fusion(
        [
            [hit["chunk_id"] for hit in vector_hits],
            [hit["chunk_id"] for hit in keyword_hits],
        ],
        k=cfg.RAG_RRF_K,
    )
    fused = apply_rank_boosts(fused, meta, question)
    ordered = sorted(fused, key=fused.get, reverse=True)[:fetch_k]
    if not ordered:
        return [], ""

    missing = [chunk_id for chunk_id in ordered if chunk_id not in embeddings]
    if missing:
        embeddings.update(store_pg.fetch_embeddings(missing))
    usable = [chunk_id for chunk_id in ordered if chunk_id in embeddings]

    if cfg.RAG_MMR_ENABLED and len(usable) > top_k:
        matrix = np.vstack([embeddings[chunk_id] for chunk_id in usable])
        similarity = matrix @ np.asarray(query_vector, dtype="float32").reshape(-1)
        picked = _mmr_indices(similarity, matrix, top_k, cfg.RAG_MMR_LAMBDA)
        chosen = [usable[index] for index in picked]
    else:
        chosen = usable[:top_k]

    rows = store_pg.fetch_chunks(chosen)
    sources: list[SourceRef] = []
    lines: list[str] = []
    for label, row in enumerate(rows, start=1):
        text = str(row.get("text") or "").strip()
        preview = text[:280] + ("…" if len(text) > 280 else "")
        public_url = (str(row.get("public_url") or "")).strip()
        is_public = bool(row.get("is_public")) and bool(public_url)
        year = row.get("publication_year")
        source = SourceRef(
            label=label,
            chunk_id=str(row.get("chunk_id") or ""),
            doc_id=str(row.get("doc_id") or ""),
            source_path=str(row.get("source_path") or ""),
            title=(str(row.get("title") or "")).strip() or None,
            section=str(row.get("section") or ""),
            location_start=int(row.get("page_start") or 1),
            location_end=int(row.get("page_end") or 1),
            preview=preview,
            public_url=public_url if is_public else None,
            is_public=is_public,
            authority_tier=str(row.get("authority_tier") or ""),
            superseded=bool(row.get("superseded")),
            publication_year=int(year) if year else None,
        )
        sources.append(source)
        bits = [source.title or source.doc_id]
        if source.publication_year:
            bits.append(str(source.publication_year))
        if source.section:
            bits.append(source.section)
        bits.append(f"pages {source.location_start}–{source.location_end}")
        if source.authority_tier:
            bits.append(source.authority_tier)
        if source.superseded:
            bits.append("superseded")
        lines.append(f"[{label}] ({', '.join(bits)}):\n{text}\n")
    return sources, "\n\n".join(lines)


def answer_question(
    user_message: str,
    top_k: int | None = None,
    history: list[ChatTurn] | None = None,
) -> RAGResult:
    k = int(top_k or cfg.RAG_TOP_K)
    client = emb.get_client()
    search = retrieval_query(user_message, history)
    sources, context = retrieve(client, search, k)
    if not context:
        return RAGResult(
            answer=(
                "I do not have any indexed LandIS passages to search yet. "
                "The corpus needs to be OCRed, checked, and indexed first."
            ),
            sources=[],
            top_k=k,
        )
    notes = []
    if looks_like_point_location(user_message):
        notes.append(
            "This is a point-location question. Do not name a soil for that place. "
            f"Direct the user to the portal map: {cfg.PORTAL_MAP_URL}."
        )
    if looks_like_regulatory(user_message):
        notes.append(
            "This asks for a regulatory or legal judgement. Decline and stay with "
            "what the sourced documents say about the data."
        )
    note = ("\n".join(notes) + "\n\n") if notes else ""
    user_block = f"{note}Question: {user_message}\n\nContext:\n{context}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt()}]
    for turn in history or []:
        messages.append({"role": "user", "content": turn.question})
        messages.append({"role": "assistant", "content": turn.answer})
    messages.append({"role": "user", "content": user_block})

    def call():
        return client.chat.complete(
            model=cfg.MISTRAL_CHAT_MODEL,
            messages=messages,
            temperature=cfg.CHAT_TEMPERATURE,
        )

    from .retry import call_with_retry

    chat = call_with_retry(call)
    if not chat or not chat.choices:
        return RAGResult(answer="No response from the model.", sources=sources, top_k=k)
    text = _assistant_text(chat.choices[0].message.content)
    return RAGResult(answer=text.strip(), sources=sources, top_k=k)
