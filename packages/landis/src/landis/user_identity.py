"""
Client identifiers for conversation logs.

IP addresses and user-agent strings are never stored. The visitor column
is the constant ``anonymous``.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ANONYMOUS_FINGERPRINT = "anonymous"


@dataclass(frozen=True)
class ClientMetadata:
    thread_id: str
    session_id: str
    visitor_fingerprint: str = ANONYMOUS_FINGERPRINT
    client_type: str = "webapp"

    @staticmethod
    def anonymous() -> ClientMetadata:
        return ClientMetadata(thread_id="anonymous", session_id="anonymous")


def metadata_from_request(*, session_id: str, client_type: str = "api") -> ClientMetadata:
    """Build metadata from a session id. Request headers are ignored on purpose."""
    ident = session_id.strip() or "anonymous"
    return ClientMetadata(thread_id=ident, session_id=ident, client_type=client_type)


def coerce_client_metadata(value: ClientMetadata | dict[str, Any] | None) -> ClientMetadata:
    if value is None:
        return ClientMetadata.anonymous()
    if isinstance(value, ClientMetadata):
        return ClientMetadata(
            thread_id=value.thread_id or "anonymous",
            session_id=value.session_id or value.thread_id or "anonymous",
            client_type=value.client_type or "webapp",
        )
    if isinstance(value, dict):
        session = str(value.get("session_id") or value.get("thread_id") or "anonymous")
        return ClientMetadata(
            thread_id=str(value.get("thread_id") or session),
            session_id=session,
            client_type=str(value.get("client_type") or "webapp"),
        )
    return ClientMetadata.anonymous()
