"""
app/api/routes/openwebui_integration.py
----------------------------------------
Handles automatic Open WebUI model registration and user provisioning.

On startup, register_rag_model() calls the Open WebUI admin API to:
  1. Obtain an admin session token from Open WebUI.
  2. Register (or update) the Enterprise RAG "model" as an OpenAI-compatible
     connection pointing to this FastAPI backend's /api/v1/chat endpoint.

This means users never need to manually configure a model in Open WebUI —
it is wired up automatically when the backend starts.

Routes:
  GET /api/v1/owui/status   — Check if Open WebUI is reachable
  POST /api/v1/owui/sync    — Force re-registration of the RAG model (admin)
"""

import logging
import asyncio

import httpx
from fastapi import APIRouter, Depends

from app.core.dependencies import require_admin, UserContext
from app.utils.properties_loader import props

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_owui_admin_token() -> str | None:
    """
    Sign in to Open WebUI as the admin user and return the session token.
    Returns None on failure — never raises so startup is not blocked.
    """
    owui_url  = props.get("openwebui.internal_url", "http://open-webui:8080")
    email     = props.get("openwebui.admin_email",    "admin@enterprise-rag.local")
    password  = props.get("openwebui.admin_password",  "ChangeMe123!")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{owui_url}/api/v1/auths/signin",
                json={"email": email, "password": password},
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token") or data.get("access_token")
                if token:
                    logger.debug("Obtained Open WebUI admin token.")
                    return token
            logger.warning(
                "Open WebUI signin returned HTTP %d: %s",
                resp.status_code, resp.text[:200],
            )
    except Exception as exc:
        logger.warning("Could not reach Open WebUI to obtain admin token: %s", exc)
    return None


async def register_rag_model() -> bool:
    """
    Register (or update) the Enterprise RAG model in Open WebUI.

    Called once at FastAPI startup and via POST /api/v1/owui/sync.

    The model is registered as an "OpenAI compatible" connection:
        API Base URL : http://backend:8000/api/v1/chat
        API Key      : (set to a placeholder; Nginx injects the real JWT)
        Model ID     : enterprise-rag

    Returns True on success, False on failure.
    """
    owui_url    = props.get("openwebui.internal_url", "http://open-webui:8080")
    model_name  = props.get("openwebui.model_name",  "Enterprise RAG")
    # The backend URL as seen from WITHIN the Docker network
    backend_url = "http://backend:8000/api/v1/chat"

    token = await _get_owui_admin_token()
    if not token:
        logger.warning("Skipping Open WebUI model registration — cannot authenticate.")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    # Open WebUI stores OpenAI-compatible connections as "connections"
    connection_payload = {
        "name":    model_name,
        "url":     backend_url,
        "api_key": "enterprise-rag-internal",   # placeholder; Nginx adds real JWT
        "models": [
            {
                "id":   "enterprise-rag",
                "name": model_name,
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Try to update an existing connection first, fall back to create
            list_resp = await client.get(
                f"{owui_url}/api/v1/connections/",
                headers=headers,
            )
            existing_id = None
            if list_resp.status_code == 200:
                for conn in list_resp.json():
                    if conn.get("url") == backend_url:
                        existing_id = conn.get("id")
                        break

            if existing_id:
                resp = await client.put(
                    f"{owui_url}/api/v1/connections/{existing_id}",
                    json=connection_payload,
                    headers=headers,
                )
            else:
                resp = await client.post(
                    f"{owui_url}/api/v1/connections/",
                    json=connection_payload,
                    headers=headers,
                )

            if resp.status_code in (200, 201):
                logger.info(
                    "Open WebUI model '%s' registered/updated successfully.",
                    model_name,
                )
                return True
            else:
                logger.warning(
                    "Open WebUI model registration returned HTTP %d: %s",
                    resp.status_code, resp.text[:300],
                )
    except Exception as exc:
        logger.warning("Open WebUI model registration failed: %s", exc)

    return False


async def provision_owui_user(user_ctx: dict) -> bool:
    """
    Ensure the user exists in Open WebUI.
    Called by the openwebui-auth-proxy after it receives the one-time token.

    Creates the user if they don't exist, or updates their name/role if they do.
    Returns True on success.
    """
    owui_url = props.get("openwebui.internal_url", "http://open-webui:8080")
    email    = user_ctx.get("email") or f"user{user_ctx['user_id']}@enterprise-rag.local"
    name     = user_ctx.get("display_name", "Enterprise User")
    is_admin = user_ctx.get("is_admin", False)
    role     = "admin" if is_admin else "user"

    admin_token = await _get_owui_admin_token()
    if not admin_token:
        return False

    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type":  "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Check if user exists
            users_resp = await client.get(f"{owui_url}/api/v1/users/", headers=headers)
            existing_user = None
            if users_resp.status_code == 200:
                for u in users_resp.json():
                    if u.get("email") == email:
                        existing_user = u
                        break

            if existing_user:
                # User exists — update role if needed
                if existing_user.get("role") != role:
                    await client.post(
                        f"{owui_url}/api/v1/users/{existing_user['id']}/update",
                        json={"role": role},
                        headers=headers,
                    )
                logger.debug("OWUI user exists for email=%s", email)
                return True
            else:
                # Create new user
                resp = await client.post(
                    f"{owui_url}/api/v1/auths/add",
                    json={
                        "name":     name,
                        "email":    email,
                        "password": f"rag-{user_ctx['user_id']}-auto",
                        "role":     role,
                    },
                    headers=headers,
                )
                if resp.status_code in (200, 201):
                    logger.info("Created OWUI user for email=%s", email)
                    return True
                logger.warning(
                    "OWUI user creation failed HTTP %d: %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.warning("OWUI user provisioning failed: %s", exc)

    return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status", summary="Check Open WebUI connectivity")
async def owui_status(user: UserContext = Depends(require_admin)):
    """Returns connectivity status of the Open WebUI service."""
    owui_url = props.get("openwebui.internal_url", "http://open-webui:8080")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{owui_url}/health")
            return {
                "reachable":   True,
                "status_code": resp.status_code,
                "url":         owui_url,
            }
    except Exception as exc:
        return {"reachable": False, "error": str(exc), "url": owui_url}


@router.post("/sync", summary="Force re-register RAG model in Open WebUI")
async def force_sync(user: UserContext = Depends(require_admin)):
    """Re-registers the Enterprise RAG model in Open WebUI."""
    success = await register_rag_model()
    if success:
        return {"message": "Open WebUI model registration successful."}
    return {"message": "Open WebUI model registration failed — check logs."}
