"""
app/core/dependencies.py
------------------------
Reusable FastAPI dependency functions for JWT authentication and RBAC.

Usage in any route:
    from app.core.dependencies import get_current_user, require_admin

    @router.get("/protected")
    async def endpoint(user: UserContext = Depends(get_current_user)):
        ...

    @router.delete("/admin-only")
    async def admin_endpoint(user: UserContext = Depends(require_admin)):
        ...
"""

import logging
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.jwt_auth import verify_token
from app.utils.properties_loader import props

logger = logging.getLogger(__name__)

# HTTPBearer reads the Authorization: Bearer <token> header automatically.
# auto_error=False means we handle the missing-token case ourselves so we
# can return a clean JSON error instead of FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False)

# Type alias — keeps route signatures readable
UserContext = dict[str, Any]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserContext:
    """
    Extract and validate the JWT Bearer token from the Authorization header.

    In dev_mode (app.dev_mode=true) the token is not required and a
    synthetic admin user is returned automatically.

    Returns:
        user_context dict with keys:
            user_id, jwt_subject, display_name, email, roles,
            is_admin, is_dev

    Raises:
        HTTP 401 — token missing or invalid
        HTTP 403 — user not found in database or inactive
    """
    # Dev mode: bypass all auth
    if props.get_bool("app.dev_mode"):
        return {
            "user_id": 1,
            "jwt_subject": "dev-user-001",
            "display_name": "Dev User (No Auth)",
            "email": "dev@local",
            "roles": ["admin"],
            "is_admin": True,
            "is_dev": True,
        }

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing. Provide: Bearer <jwt_token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    ok, user_ctx, error_msg = verify_token(credentials.credentials)

    if not ok or user_ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_msg or "Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_ctx


async def require_admin(
    user: UserContext = Depends(get_current_user),
) -> UserContext:
    """
    Extends get_current_user — additionally enforces the 'admin' role.

    Raises:
        HTTP 403 — authenticated but not an admin
    """
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required for this operation.",
        )
    return user
