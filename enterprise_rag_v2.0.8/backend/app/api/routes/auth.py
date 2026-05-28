"""
app/api/routes/auth.py
----------------------
Authentication endpoints.

POST /api/v1/auth/verify
    Validates a JWT (passed as Bearer or as ?token= query param).
    Returns the resolved user context.  Used by Nginx's auth_request
    sub-request to gate all /api/* routes.

GET  /api/v1/auth/owui-login
    Converts an Enterprise RAG JWT into an Open WebUI auto-login URL.
    The browser is redirected to Open WebUI where the openwebui-auth-proxy
    handles the one-time token exchange and creates/logs-in the OWUI user.

GET  /api/v1/auth/me
    Returns the current authenticated user's profile.
"""

import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse, JSONResponse

from app.core.dependencies import get_current_user, UserContext
from app.core.jwt_auth import verify_token
from app.utils.properties_loader import props

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# One-time auto-login token store
# { token_str: { user_ctx, expires_at } }
# ---------------------------------------------------------------------------
_owui_tokens: dict[str, dict[str, Any]] = {}
_TOKEN_TTL = 60  # seconds — short-lived, single-use


def _purge_expired_tokens() -> None:
    """Remove expired auto-login tokens (called on every issuance)."""
    now = time.time()
    expired = [k for k, v in _owui_tokens.items() if v["expires_at"] < now]
    for k in expired:
        del _owui_tokens[k]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/verify", summary="Verify JWT — used by Nginx auth_request")
async def verify(token: str = Query(None)):
    """
    Nginx auth_request sub-request endpoint.

    Nginx sends a sub-request here before forwarding to any protected upstream.
    Returns 200 with user info headers on success, 401/403 on failure.

    The token can come from:
      - Authorization: Bearer <token>   header (standard API calls)
      - ?token=<jwt>                    query param  (browser deep-link flow)
    """
    if not token:
        raise HTTPException(status_code=401, detail="Token required")

    ok, user_ctx, err = verify_token(token)
    if not ok or not user_ctx:
        raise HTTPException(status_code=401, detail=err or "Invalid token")

    return JSONResponse(
        status_code=200,
        content={
            "user_id": user_ctx["user_id"],
            "display_name": user_ctx["display_name"],
            "email": user_ctx["email"],
            "roles": user_ctx["roles"],
            "is_admin": user_ctx["is_admin"],
        },
        headers={
            # Nginx can forward these as X-* headers to upstream services
            "X-User-Id":   str(user_ctx["user_id"]),
            "X-User-Name": user_ctx["display_name"],
            "X-User-Email": user_ctx.get("email", ""),
            "X-User-Roles": ",".join(user_ctx.get("roles", [])),
            "X-Is-Admin":  "true" if user_ctx["is_admin"] else "false",
        },
    )


@router.get("/owui-login", summary="Generate Open WebUI auto-login redirect")
async def owui_login(token: str = Query(..., description="Enterprise RAG JWT")):
    """
    Token → Open WebUI auto-login flow.

    1. Validate the incoming Enterprise RAG JWT.
    2. Generate a short-lived (60 s) single-use token tied to the user.
    3. Redirect browser to /auth-proxy/login?token=<one-time-token>
       where the openwebui-auth-proxy completes the Open WebUI login.

    Deep-link usage:
        http://your-server/?token=<enterprise_jwt>
    Nginx rewrites this to:
        /api/v1/auth/owui-login?token=<enterprise_jwt>
    """
    ok, user_ctx, err = verify_token(token)
    if not ok or not user_ctx:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=err or "Invalid or expired token.",
        )

    _purge_expired_tokens()

    # Issue a short-lived one-time token
    one_time = secrets.token_urlsafe(32)
    _owui_tokens[one_time] = {
        "user_ctx": user_ctx,
        "expires_at": time.time() + _TOKEN_TTL,
        "used": False,
    }

    # Redirect to the auth proxy which completes the Open WebUI login
    proxy_url = f"/auth-proxy/login?token={one_time}"
    logger.info(
        "Issued OWUI auto-login token for user_id=%s display_name=%s",
        user_ctx["user_id"], user_ctx["display_name"],
    )
    return RedirectResponse(url=proxy_url, status_code=302)


@router.get("/owui-token/{one_time_token}", summary="Exchange one-time token for user context")
async def exchange_owui_token(one_time_token: str):
    """
    Called ONLY by the openwebui-auth-proxy (server-to-server, not browser).

    Validates and consumes the one-time token, returning the user context
    so the proxy can create/update the Open WebUI user and obtain a session.

    Security:
      - Token is single-use (marked used on first exchange).
      - Token expires after 60 seconds.
      - Only callable from within the Docker network (Nginx blocks external access).
    """
    _purge_expired_tokens()

    record = _owui_tokens.get(one_time_token)
    if not record:
        raise HTTPException(status_code=404, detail="Token not found or expired.")

    if record.get("used"):
        raise HTTPException(status_code=410, detail="Token already used.")

    if time.time() > record["expires_at"]:
        del _owui_tokens[one_time_token]
        raise HTTPException(status_code=410, detail="Token expired.")

    # Consume the token — one-time use only
    record["used"] = True

    logger.info(
        "OWUI one-time token consumed for user_id=%s",
        record["user_ctx"]["user_id"],
    )
    return record["user_ctx"]


@router.get("/me", summary="Current user profile")
async def me(user: UserContext = Depends(get_current_user)):
    """Returns the authenticated user's profile extracted from the JWT."""
    return {
        "user_id":      user["user_id"],
        "display_name": user["display_name"],
        "email":        user.get("email"),
        "roles":        user.get("roles", []),
        "is_admin":     user.get("is_admin", False),
        "is_dev":       user.get("is_dev", False),
    }
