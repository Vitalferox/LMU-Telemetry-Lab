from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .config import get_settings

logger = logging.getLogger(__name__)

# Profile bound to the bearer token of the request being handled, when the
# token carries one. Read by get_contextual_dirs to pin data access to the
# authenticated user instead of trusting the client-supplied profile_id.
_authenticated_profile: ContextVar[str | None] = ContextVar("authenticated_profile", default=None)


def get_authenticated_profile() -> str | None:
    return _authenticated_profile.get()

# ---------------------------------------------------------------------------
# Path sanitisation
# ---------------------------------------------------------------------------

_PATH_TRAVERSAL = re.compile(r"(^|[\\/])\.\.($|[\\/])")


def safe_session_id(session_id: str) -> str:
    """Reject session IDs that could escape the data directory."""
    name = os.path.basename(session_id)
    if name != session_id or _PATH_TRAVERSAL.search(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")
    return name


# ---------------------------------------------------------------------------
# Auth middleware — token-based, skipped in local mode
# ---------------------------------------------------------------------------

# Endpoints that never require auth (health probes, static assets)
_PUBLIC_PREFIXES = ("/health", "/api/v1/health", "/api/v1/ping")


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()

        # Local mode or no tokens configured → pass through
        if not settings.is_server or not settings.auth_token_set:
            return await call_next(request)

        path = request.url.path

        # Public endpoints
        if any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # Static frontend assets (not /api/)
        if not path.startswith("/api/"):
            return await call_next(request)

        # Extract token from Authorization header
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        else:
            token = ""

        if token not in settings.auth_token_set:
            logger.warning("Auth rejected for %s %s from %s", request.method, path, request.client.host if request.client else "?")
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        reset_token = _authenticated_profile.set(settings.token_to_profile.get(token))
        try:
            return await call_next(request)
        finally:
            _authenticated_profile.reset(reset_token)


# ---------------------------------------------------------------------------
# Guard: block dangerous endpoints in server mode
# ---------------------------------------------------------------------------

# Endpoints that should only run on the local desktop app
_LOCAL_ONLY_PATHS = {
    "/api/v1/system/open-path",
    "/api/v1/system/pick-and-upload",
    "/api/v1/debug/env",
    "/api/v1/system/damplugin/activate",
    "/api/v1/system/damplugin/deactivate",
}


class LocalOnlyGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if settings.is_server and request.url.path in _LOCAL_ONLY_PATHS:
            return JSONResponse(
                {"detail": "This endpoint is disabled in server mode"},
                status_code=403,
            )
        return await call_next(request)
