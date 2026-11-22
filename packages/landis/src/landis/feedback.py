"""
Answer feedback, written to one JSON Lines file per UTC day.

Files go to ``FEEDBACK_DIR`` (default ``logs/feedback/``), as ``YYYY-MM-DD.jsonl``.
No IP address, user agent, or cookie value is written.

**Created:** 22-11-2026 (UK style).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config as cfg

REASONS: tuple[str, ...] = ("incorrect", "not_answered", "too_technical", "too_simple")

_write_lock = threading.Lock()


def feedback_path(when: datetime | None = None) -> Path:
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return cfg.FEEDBACK_DIR / f"{day}.jsonl"


def save_feedback(record: dict[str, Any], when: datetime | None = None) -> Path:
    """Append one rating. Raises ``OSError`` if the file cannot be written."""
    path = feedback_path(when)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return path
