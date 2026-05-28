"""
jwt_auth.py
-----------
JWT authentication for the Enterprise RAG application.

Tokens arrive as a URL query parameter (?token=...).
The public key is loaded once and cached for the lifetime of the process.

In dev_mode (app.dev_mode=true in properties) the JWT check is bypassed
and a synthetic dev user is returned so developers can run the app locally
without a real IdP.
"""

import logging
from pathlib import Path
from typing import Any

from app.utils.properties_loader import props
from app.db.user_repository import get_user_by_jwt_subject, get_user_roles

logger = logging.getLogger(__name__)

# Cached public key — loaded once on first verification
_public_key: str | None = None


def _load_public_key() -> str | None:
    """
    Read the RSA public key PEM file from the path in app.properties.

    The key is loaded once and cached for the process lifetime.
    Returns None (with a warning) if the path is not configured or the
    file does not exist — callers treat None as an auth configuration error.
    """
    global _public_key
    if _public_key is not None:
        return _public_key

    key_path_str = props.get("jwt.public_key_path")
    if not key_path_str:
        logger.error(
            "jwt.public_key_path is not set in conf/app.properties. "
            "JWT verification will fail until this is configured."
        )
        return None

    key_path = Path(key_path_str)
    if not key_path.exists():
        logger.warning(
            "JWT public key file not found at '%s'. "
            "Ensure the file exists or set app.dev_mode=true for development.",
            key_path,
        )
        return None

    try:
        _public_key = key_path.read_text(encoding="utf-8")
        logger.info("JWT public key loaded from %s", key_path)
        return _public_key
    except Exception as exc:
        logger.error("Failed to read JWT public key from '%s': %s", key_path, exc)
        return None


def _dev_user() -> dict[str, Any]:
    """Return a synthetic user for development mode."""
    return {
        "user_id": 1,
        "jwt_subject": "dev-user-001",
        "display_name": "Dev User (No Auth)",
        "email": "dev@local",
        "roles": ["admin"],  # Admin in dev so all features are accessible
        "is_admin": True,
        "is_dev": True,
    }


def verify_token(token: str) -> tuple[bool, dict[str, Any] | None, str]:
    """
    Validate a JWT token and return the associated user context.

    Returns:
        (success, user_context, error_message)

    user_context keys: user_id, jwt_subject, display_name, email, roles, is_admin
    """
    # -----------------------------------------------------------------------
    # Dev mode bypass — NEVER enable in production
    # -----------------------------------------------------------------------
    if props.get_bool("app.dev_mode"):
        logger.warning("DEV MODE: JWT verification bypassed.")
        return True, _dev_user(), ""

    # -----------------------------------------------------------------------
    # Real JWT verification
    # -----------------------------------------------------------------------
    if not token:
        return False, None, "Authentication token is missing."

    try:
        import jwt as pyjwt  # PyJWT
    except ImportError:
        logger.error("PyJWT is not installed. Add 'PyJWT[crypto]' to requirements.txt")
        return False, None, "Server authentication library is not available."

    public_key = _load_public_key()
    if public_key is None:
        return False, None, "Server authentication configuration error."

    try:
        algorithm = props.get("jwt.algorithm")
        audience = props.get("jwt.audience") or None
        issuer = props.get("jwt.issuer") or None
        leeway = props.get_int("jwt.leeway_seconds")

        options = {}
        decode_kwargs: dict[str, Any] = {
            "algorithms": [algorithm],
            "options": options,
            "leeway": leeway,
        }
        if audience:
            decode_kwargs["audience"] = audience
        if issuer:
            decode_kwargs["issuer"] = issuer

        payload = pyjwt.decode(token, public_key, **decode_kwargs)

    except pyjwt.ExpiredSignatureError:
        return False, None, "Your session has expired. Please re-authenticate."
    except pyjwt.InvalidAudienceError:
        return False, None, "Token audience mismatch."
    except pyjwt.InvalidIssuerError:
        return False, None, "Token issuer mismatch."
    except pyjwt.InvalidTokenError as exc:
        logger.warning("JWT validation failed: %s", exc)
        return False, None, "Invalid authentication token."

    # -----------------------------------------------------------------------
    # Look up the user in SQL Server
    # -----------------------------------------------------------------------
    jwt_subject = payload.get("sub")
    if not jwt_subject:
        return False, None, "Token is missing the 'sub' claim."

    user_record = get_user_by_jwt_subject(jwt_subject)
    if user_record is None:
        return False, None, "User account not found or is inactive."

    user_id = user_record["user_id"]
    roles = get_user_roles(user_id)

    user_context = {
        "user_id": user_id,
        "jwt_subject": jwt_subject,
        "display_name": user_record["display_name"],
        "email": user_record.get("email", ""),
        "roles": roles,
        "is_admin": "admin" in roles,
        "is_dev": False,
    }

    logger.info("Authenticated user: %s roles=%s", user_record["display_name"], roles)
    return True, user_context, ""
