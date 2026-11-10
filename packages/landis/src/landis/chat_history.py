"""Recent turns used to resolve follow-up questions."""

from __future__ import annotations

from dataclasses import dataclass

from . import config as cfg


@dataclass(frozen=True)
class ChatTurn:
    question: str
    answer: str


def truncate_answer(text: str) -> str:
    limit = max(200, cfg.CHAT_HISTORY_MAX_ANSWER_CHARS)
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."
