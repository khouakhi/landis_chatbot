"""
Retrieval and answering.

Dense search and Postgres full-text search are fused with reciprocal rank
fusion, then MMR picks a diverse set. One Mistral chat call writes the answer.
There is no tool loop.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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


AUDIENCES: tuple[str, ...] = ("general", "manager", "specialist")
FORMAT_TAGS: tuple[str, ...] = ("short", "bullets", "table", "steps", "compare")
LANGUAGES: tuple[str, ...] = ("en", "cy")

_AUDIENCE_NOTES: dict[str, str] = {
    "general": (
        "Audience: general public. Use plain English and short sentences. "
        "Define technical terms the first time they appear. Keep explanations brief."
    ),
    "manager": (
        "Audience: land manager or adviser. Be practical and management-focused. "
        "Say what the information means for decisions on the ground. Keep explanations brief."
    ),
    "specialist": (
        "Audience: soil specialist. Give full technical detail and use the correct "
        "terminology. Do not simplify."
    ),
}

_FORMAT_NOTES: dict[str, str] = {
    "short": "Format: keep the answer short, a few sentences or about 80 words at most.",
    "bullets": "Format: give the answer mainly as bullet points.",
    "table": (
        "Format: include a GitHub-flavoured Markdown table that summarises the answer, "
        "if the sources support one. Each row on its own line, with a separator row."
    ),
    "steps": "Format: give the answer as numbered steps.",
    "compare": (
        "Format: compare the options side by side in a Markdown table, with one column "
        "per option, if the sources support a comparison."
    ),
}

_LANGUAGE_NOTES: dict[str, str] = {
    "en": "Write the whole answer in English, with UK spelling.",
    "cy": (
        "Write the whole answer in Welsh (Cymraeg), including the follow-up questions. "
        "Keep document titles, section names, and place names as they appear in the sources."
    ),
}


def system_prompt(
    audience: str = "general",
    formats: Sequence[str] = (),
    lang: str = "en",
) -> str:
    """Build the system prompt. Audience, format, and language change style only."""
    portal = cfg.PORTAL_MAP_URL
    head = (
        "You are the LandIS assistant for the National Soil Data Portal for England and Wales. "
        "You answer questions about soil data, soil classification, soil survey reports, "
        "and how to use the portal. "
        "Answer only using the numbered context excerpts. If the context does not contain "
        "the answer, say that the corpus does not cover it. "
        "Cite the excerpts you actually use, with markers such as [1] or [2, 3] next to "
        "the sentence they support. Use only the excerpt numbers shown at the start of "
        "each excerpt, and no more than two markers for one statement. Never put soil "
        "series, association or map-unit codes, such as 541m, in square brackets. "
        "In the same sentence, name the document title, the "
        "section, and the page when the context gives them. "
        "Every sentence that states a fact ends with its [n] marker. When excerpts give "
        "different figures for the same thing, give the figure from the LandIS definitive "
        "source first, and say that another source gives a different figure. "
        "Prefer LandIS definitive sources. If a source is marked superseded, say so and "
        "give its publication year. "
        "Do not give site-specific advice about what soil is at a place, a postcode, or a "
        f"grid reference. Point the user to the portal map instead: {portal}. "
        "Do not interpret regulations, planning rules, or what a person is legally required to do. "
        "The source corpus is in English. "
        "Do not write a link to the LandIS Soils Guide or a 'More detail' line. "
        "The chat adds that line itself. "
        "Use Markdown: one ### heading, then bold **Section title:** lines, then bullets. "
        "Use bold only for a section title on its own line, such as **Overview:**, and for "
        "a component name on its own line. Never bold a bullet label, a sentence or a phrase "
        "inside a sentence. "
        "and never end a sentence with a colon. "
        "Do not use heading levels deeper than ###. "
        "When a table helps, use GitHub-flavoured Markdown with each row on its own line "
        "and a separator row. Never put a whole table on one line."
    )

    style_notes = [
        _AUDIENCE_NOTES.get(audience, _AUDIENCE_NOTES["general"]),
        _LANGUAGE_NOTES.get(lang, _LANGUAGE_NOTES["en"]),
    ]
    for tag in formats:
        if tag in _FORMAT_NOTES:
            style_notes.append(_FORMAT_NOTES[tag])
    style_notes.append(
        "These style notes change how the answer is written, not which sources are used. "
        "The citation and grounding rules above still apply in full."
    )

    tail = (
        "After the answer, append exactly three follow-up questions inside this block "
        "and do not put citations inside it. Write the follow-up questions in the same "
        "language as the answer:\n"
        "<<SUGGESTED>>\n"
        "- First follow-up question?\n"
        "- Second follow-up question?\n"
        "- Third follow-up question?\n"
        "<<END>>"
    )
    return "\n\n".join([head, *style_notes, tail])


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
    # Structured records only: the record's Soils Guide page and its title.
    record_url: str | None = None
    record_title: str | None = None


@dataclass
class RAGResult:
    answer: str
    sources: list[SourceRef]
    top_k: int


def _page_number(value) -> int:
    """Return the stored page number. Missing means page 1; 0 means no page."""
    return 1 if value is None else int(value)


def looks_like_point_location(question: str) -> bool:
    """True for a postcode, an eight-figure grid, or a 'what soil is here' question."""
    text = question or ""
    if _POSTCODE.search(text) or _GRID.search(text):
        return True
    lowered = text.lower()
    return "what soil" in lowered and any(
        token in lowered for token in ("postcode", "grid reference", "this field", "my garden", "where i am")
    )


_DEFINITION = re.compile(
    r"^\s*(what\s+(is|are|'s)|define|definition\s+of|meaning\s+of|what\s+does\b.*\bmean)\b",
    re.IGNORECASE,
)


def looks_like_definition(question: str) -> bool:
    """True for a short 'what is X' or 'define X' question. Longer questions ask for facts."""
    text = question or ""
    return len(text.split()) <= 6 and bool(_DEFINITION.search(text))


_COMPARISON = re.compile(
    r"\b(compare|compares|compared|comparison|versus|vs\.?|difference between|"
    r"differences between|differ|contrast)\b",
    re.IGNORECASE,
)


def looks_like_comparison(question: str) -> bool:
    """True for a question that compares two or more items."""
    return bool(_COMPARISON.search(question or ""))


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


def retrieve(
    client,
    question: str,
    top_k: int,
    pinned: Sequence[str] = (),
) -> tuple[list[SourceRef], str]:
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

    if pinned:
        # A record the user named by its code always comes first, as excerpt [1].
        chosen = list(pinned) + [chunk_id for chunk_id in chosen if chunk_id not in pinned]
        chosen = chosen[: max(top_k, len(pinned))]
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
            # Structured database records store page 0: they have no pages to cite.
            location_start=_page_number(row.get("page_start")),
            location_end=_page_number(row.get("page_end")),
            preview=preview,
            public_url=public_url if is_public else None,
            is_public=is_public,
            authority_tier=str(row.get("authority_tier") or ""),
            superseded=bool(row.get("superseded")),
            publication_year=int(year) if year else None,
            record_url=(str(row.get("source_url") or "")).strip() or None,
            record_title=(str(row.get("source_title") or "")).strip() or None,
        )
        sources.append(source)
        bits = [source.title or source.doc_id]
        if source.publication_year:
            bits.append(str(source.publication_year))
        if source.section:
            bits.append(source.section)
        if source.location_start:
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
    *,
    audience: str = "general",
    formats: Sequence[str] = (),
    lang: str = "en",
    pinned_chunk_ids: Sequence[str] = (),
) -> RAGResult:
    k = int(top_k or cfg.RAG_TOP_K)
    client = emb.get_client()
    search = retrieval_query(user_message, history)
    # Retrieval uses the question only. Audience, format, and language affect the wording.
    sources, context = retrieve(client, search, k, pinned_chunk_ids)
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
    count = len(pinned_chunk_ids)
    if count == 1:
        notes.append(
            "The user named one association by its code. Excerpt [1] is the database "
            "record for it. Answer from that record, cite [1], and do not add other "
            "associations."
        )
    elif count > 1:
        notes.append(
            f"The user named {count} associations by their codes. Excerpts [1] to [{count}] "
            "are the database records for them. Answer only from these records. Do not add "
            "other associations. If a named association has no record, say so."
        )
    if looks_like_comparison(user_message):
        notes.append(
            "This is a comparison. Present the answer as a Markdown table with one row for "
            "each item compared and a column for each aspect the user asks about. Cite the "
            "record in each row. If a figure is not in the excerpts, write 'not in the "
            "excerpts' in that cell rather than guessing. Add at most one sentence before "
            "or after the table."
        )
    elif not pinned_chunk_ids and looks_like_definition(user_message):
        notes.append(
            "This is a definition question. Define the term from the definition sources "
            "only, using one or two excerpts. Do not cite individual soil series or "
            "association records as examples unless the user asks for one."
        )
    if looks_like_regulatory(user_message):
        notes.append(
            "This asks for a regulatory or legal judgement. Decline and stay with "
            "what the sourced documents say about the data."
        )
    note = ("\n".join(notes) + "\n\n") if notes else ""
    user_block = f"{note}Question: {user_message}\n\nContext:\n{context}"
    style = system_prompt(audience=audience, formats=formats, lang=lang)
    messages: list[dict[str, str]] = [{"role": "system", "content": style}]
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
