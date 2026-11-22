"""
Daily question limit per visitor. No accounts.

Visitors are counted by a random session cookie. The cookie value is hashed
(SHA-256) before it reaches the database. IP addresses and headers are not used.
The day boundary is UTC midnight.

**Created:** 22-11-2026 (UK style).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import config as cfg
from . import store_pg


@dataclass(frozen=True)
class Usage:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def as_dict(self) -> dict[str, int]:
        return {"used": self.used, "limit": self.limit, "remaining": self.remaining}


def new_visitor_token() -> str:
    return secrets.token_urlsafe(24)


def is_valid_visitor_token(token: str | None) -> bool:
    if not token or not 16 <= len(token) <= 128:
        return False
    return token.replace("-", "").replace("_", "").isalnum()


def visitor_key(token: str) -> str:
    """Hashed cookie value. This is the only visitor identifier stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def current_usage(token: str) -> Usage:
    count = store_pg.get_daily_question_count(today_utc(), visitor_key(token))
    return Usage(used=count, limit=cfg.DAILY_QUESTION_LIMIT)


def consume_question(token: str) -> Usage | None:
    """
    Count one question for this visitor.

    Returns ``None`` when the limit is already reached. In that case the caller
    must not call the model.
    """
    limit = cfg.DAILY_QUESTION_LIMIT
    count = store_pg.consume_daily_question(today_utc(), visitor_key(token), limit)
    if count is None:
        return None
    return Usage(used=count, limit=limit)
