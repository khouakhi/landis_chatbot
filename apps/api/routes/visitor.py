"""
Anonymous visitor cookie for the daily question limit.

The cookie is HttpOnly and random. It is set with SameSite=None and Secure so it
still works when the chat is framed on another site (the portal iframe).

**Created:** 22-11-2026 (UK style).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, Response

from landis import config as cfg
from landis import daily_limit

logger = logging.getLogger(__name__)

_MAX_AGE_SECONDS = cfg.VISITOR_COOKIE_MAX_AGE_DAYS * 24 * 60 * 60


def visitor_token(request: Request, response: Response) -> str:
    """Return the visitor token, issuing a new one if the cookie is missing or malformed."""
    token = request.cookies.get(cfg.VISITOR_COOKIE_NAME)
    if not daily_limit.is_valid_visitor_token(token):
        token = daily_limit.new_visitor_token()
    response.set_cookie(
        key=cfg.VISITOR_COOKIE_NAME,
        value=token,
        max_age=_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=True,
        samesite="none",
    )
    return token


def counter_unavailable(exc: Exception) -> HTTPException:
    """The usage counter needs the database. Fail closed rather than skip the limit."""
    logger.error("Daily usage counter failed: %s", exc, exc_info=True)
    return HTTPException(
        status_code=503,
        detail="The daily question counter is unavailable. Please try again shortly.",
    )
