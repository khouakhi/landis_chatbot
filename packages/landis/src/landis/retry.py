"""
Retry Mistral calls that fail because of rate limits or brief outages.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    return None


def is_retryable(exc: BaseException) -> bool:
    """True for HTTP 429 and transient 5xx responses."""
    code = _status_code(exc)
    if code in {429, 500, 502, 503, 504}:
        return True
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "1300" in text


def call_with_retry(fn: Callable[[], T], *, attempts: int = 4, base_delay: float = 1.0) -> T:
    """Call ``fn`` and back off when the failure looks transient."""
    last: BaseException | None = None
    delay = base_delay
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt == attempts - 1 or not is_retryable(exc):
                raise
            time.sleep(delay)
            delay *= 2
    assert last is not None
    raise last


def quota_message(exc: BaseException) -> str | None:
    """A short message for a spent Mistral quota, or None for other errors."""
    code = _status_code(exc)
    text = str(exc)
    if code == 429 or "429" in text or "1300" in text:
        return (
            "The assistant is temporarily unavailable because the Mistral quota "
            "has been reached. Please try again later."
        )
    return None
