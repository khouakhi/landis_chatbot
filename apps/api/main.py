"""
LandIS assistant — FastAPI.

Run from the code folder:
  uv run --directory apps/api uvicorn main:app --reload --port 8080

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse, Response

from routes.chat import router as chat_router

logger = logging.getLogger(__name__)


class ShortCacheStaticFiles(StaticFiles):
    """Ask browsers to revalidate the chat page after each deploy."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if isinstance(response, Response) and path.endswith((".js", ".css", ".html")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


def _basic_credentials() -> tuple[str, str]:
    return (
        os.environ.get("LANDIS_BASIC_USER", "").strip(),
        os.environ.get("LANDIS_BASIC_PASSWORD", "").strip(),
    )


def _authorised(header: str, user: str, password: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header.split(" ", 1)[1], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    given_user, separator, given_password = raw.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(given_user, user) and secrets.compare_digest(
        given_password, password
    )


_DEFAULT_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]


def _extra_origins() -> list[str]:
    return [
        origin.strip()
        for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Drop conversation rows older than the retention window."""
    try:
        from landis.conversation_log import purge_expired_conversations

        deleted = purge_expired_conversations()
        logger.info("Conversation retention purge removed %s row(s)", deleted)
    except Exception as exc:
        logger.warning("Conversation retention purge skipped: %s", exc)
    yield


app = FastAPI(
    title="LandIS Assistant API",
    description="Cited answers from the LandIS corpus.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    """
    When both LANDIS_BASIC_USER and LANDIS_BASIC_PASSWORD are set, require them.

    /health stays open so Render can probe the service. Leave both unset for local use.
    """
    user, password = _basic_credentials()
    if not user or not password or request.url.path == "/health" or request.method == "OPTIONS":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if _authorised(header, user, password):
        return await call_next(request)
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="LandIS"'},
        content="Authentication required.",
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEFAULT_ORIGINS + _extra_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)

_web_dir = Path(__file__).resolve().parents[2] / "web"
if _web_dir.is_dir():
    app.mount(
        "/web",
        ShortCacheStaticFiles(directory=str(_web_dir), html=True),
        name="web",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/web/")
