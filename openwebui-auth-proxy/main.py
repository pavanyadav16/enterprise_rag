"""
openwebui-auth-proxy/main.py
-----------------------------
Lightweight FastAPI service that bridges Enterprise RAG JWT authentication
with Open WebUI's session system.

The /auth-proxy/health endpoint is available immediately on startup.
Open WebUI connectivity is checked in a background task — the proxy
serves login requests as soon as Open WebUI becomes reachable.
"""

import asyncio
import logging
import os

import httpx
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("auth-proxy")

# ── Configuration ─────────────────────────────────────────────────────────────
BACKEND_INTERNAL_URL = os.getenv("BACKEND_INTERNAL_URL", "http://backend:8000")
OWUI_INTERNAL_URL    = os.getenv("OWUI_INTERNAL_URL",    "http://open-webui:8080")
OWUI_ADMIN_EMAIL     = os.getenv("OWUI_ADMIN_EMAIL",     "admin@enterprise-rag.local")
OWUI_ADMIN_PASSWORD  = os.getenv("OWUI_ADMIN_PASSWORD",  "ChangeMe123!")

# ── Lifespan — starts background OWUI wait, does NOT block startup ────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start background OWUI connectivity check. Health is available immediately."""
    asyncio.create_task(_wait_for_owui())
    yield
    logger.info("Auth proxy shutting down.")


async def _wait_for_owui():
    """
    Background task — polls Open WebUI until reachable.
    Runs concurrently with request serving so healthcheck always passes.
    """
    max_wait = 180
    interval = 5
    elapsed  = 0
    logger.info("Background: waiting for Open WebUI at %s", OWUI_INTERNAL_URL)
    while elapsed < max_wait:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{OWUI_INTERNAL_URL}/health")
                if resp.status_code < 500:
                    logger.info("Open WebUI is reachable (HTTP %d) after %ds.", resp.status_code, elapsed)
                    return
        except Exception:
            pass
        await asyncio.sleep(interval)
        elapsed += interval
        if elapsed % 30 == 0:
            logger.info("Background: still waiting for Open WebUI... %ds / %ds", elapsed, max_wait)
    logger.warning("Open WebUI not reachable after %ds — login requests may fail until it starts.", max_wait)


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Open WebUI Auth Proxy",
    description="Handles Enterprise RAG to Open WebUI auto-login flow.",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_owui_admin_token() -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{OWUI_INTERNAL_URL}/api/v1/auths/signin",
            json={"email": OWUI_ADMIN_EMAIL, "password": OWUI_ADMIN_PASSWORD},
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise ValueError(f"No token in OWUI signin response: {list(data.keys())}")
        return token


async def _ensure_owui_user(user_ctx: dict, admin_token: str) -> str:
    email    = user_ctx.get("email") or f"user{user_ctx['user_id']}@enterprise-rag.local"
    name     = user_ctx.get("display_name", "Enterprise User")
    role     = "admin" if user_ctx.get("is_admin") else "user"
    password = f"rag-auto-{user_ctx['user_id']}-secret"
    headers  = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=15) as client:
        users_resp = await client.get(f"{OWUI_INTERNAL_URL}/api/v1/users/", headers=headers)
        if users_resp.status_code == 200:
            for u in users_resp.json():
                if u.get("email") == email:
                    logger.debug("OWUI user already exists: %s", email)
                    return password

        create_resp = await client.post(
            f"{OWUI_INTERNAL_URL}/api/v1/auths/add",
            json={"name": name, "email": email, "password": password, "role": role},
            headers=headers,
        )
        if create_resp.status_code not in (200, 201):
            raise RuntimeError(
                f"OWUI user creation failed HTTP {create_resp.status_code}: "
                f"{create_resp.text[:200]}"
            )
        logger.info("Created OWUI user: %s (role=%s)", email, role)
        return password


async def _sign_in_as_user(email: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{OWUI_INTERNAL_URL}/api/v1/auths/signin",
            json={"email": email, "password": password},
        )
        resp.raise_for_status()
        data  = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise ValueError("No token in OWUI user signin response")
        return token


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/auth-proxy/health")
async def health():
    """Liveness probe — always returns 200 immediately after startup."""
    return {"status": "ok", "service": "owui-auth-proxy", "version": "2.0.8"}


@app.get("/auth-proxy/login")
async def auto_login(token: str = Query(...)):
    """
    Complete the JWT auto-login flow.
    1. Exchange one-time token with backend.
    2. Provision user in Open WebUI.
    3. Sign in as that user.
    4. Redirect browser to Open WebUI root with session cookie.
    """
    # Step 1 — Exchange one-time token with backend
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{BACKEND_INTERNAL_URL}/api/v1/auth/owui-token/{token}"
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=401, detail="Login link expired or already used.")
            resp.raise_for_status()
            user_ctx = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Token exchange failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Authentication service error: {exc}")

    email    = user_ctx.get("email") or f"user{user_ctx['user_id']}@enterprise-rag.local"
    password = f"rag-auto-{user_ctx['user_id']}-secret"

    # Step 2 — Provision user in Open WebUI
    try:
        admin_token = await _get_owui_admin_token()
        await _ensure_owui_user(user_ctx, admin_token)
    except Exception as exc:
        logger.error("OWUI user provisioning failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not provision Open WebUI user: {exc}")

    # Step 3 — Sign in as the user
    try:
        session_token = await _sign_in_as_user(email, password)
    except Exception as exc:
        logger.error("OWUI user sign-in failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Could not sign in to Open WebUI: {exc}")

    logger.info("Auto-login complete for user_id=%s", user_ctx.get("user_id"))

    # Step 4 — Set session cookie and redirect to Open WebUI
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key="token",
        value=session_token,
        httponly=False,
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    logger.info("Starting auth proxy on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
