"""
llm_client.py
-------------
HTTP client for the custom-hosted LLM API.

Token lifecycle:
  1. POST /token  with username+password → receive bearer token.
  2. POST /generate-text with Authorization header → receive response.
  3. Tokens are cached in memory; a new one is fetched when the cached
     token is within `llm.token_expiry_buffer_seconds` of expiry, or when
     the API returns a 401.

All network calls use the requests library (synchronous) to keep the
integration simple and match the provided curl examples.
"""

import logging
import time
from typing import Any

import requests

from app.utils.properties_loader import props

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory token cache
# ---------------------------------------------------------------------------
_cached_token: str | None = None
_token_expires_at: float = 0.0  # UNIX timestamp


def _fetch_new_token() -> str:
    """
    POST to the token endpoint and return the bearer token string.

    Raises:
        RuntimeError: If the token request fails.
    """
    url      = props.get("llm.token_url")
    username = props.get("llm.username")
    password = props.get("llm.password")
    timeout  = props.get_int("llm.request_timeout_seconds", 120)
    if timeout < 1:
        timeout = 120

    payload = {"username": username, "password": password}

    try:
        logger.debug("Fetching new LLM token from %s", url)
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(f"Cannot reach LLM token endpoint ({url}): {exc}") from exc
    except requests.exceptions.Timeout:
        raise RuntimeError("LLM token request timed out.")
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(f"LLM token request failed (HTTP {exc.response.status_code}).") from exc
    except Exception as exc:
        raise RuntimeError(f"Unexpected error fetching LLM token: {exc}") from exc

    # The token may be under different keys depending on API implementation.
    # Try common patterns.
    token = (
        data.get("token")
        or data.get("access_token")
        or data.get("data", {}).get("token")
    )
    if not token:
        raise RuntimeError(f"Token not found in LLM response. Keys: {list(data.keys())}")

    # Determine expiry — use expires_in if provided by the API, else default 1 hour.
    expires_in = data.get("expires_in", 3600)
    buffer     = props.get_int("llm.token_expiry_buffer_seconds", 60)

    # Guard: buffer must not exceed expires_in to avoid a negative expiry time
    if buffer < 0:
        buffer = 0
    if buffer >= expires_in:
        buffer = max(0, int(expires_in) // 10)  # use 10% of token lifetime as buffer

    global _cached_token, _token_expires_at
    _cached_token = token
    _token_expires_at = time.time() + float(expires_in) - buffer

    logger.info("LLM token obtained; expires in ~%ds", expires_in)
    return token


def _get_valid_token() -> str:
    """Return a valid cached token, refreshing it if expired."""
    global _cached_token, _token_expires_at

    if _cached_token and time.time() < _token_expires_at:
        return _cached_token

    return _fetch_new_token()


def generate_response(user_prompt: str, system_prompt: str | None = None) -> str:
    """
    Call the LLM text-generation endpoint and return the assistant's reply.

    This function manages the full request lifecycle:
      1. Acquires a valid bearer token (refreshing if expired).
      2. Sends the system + user prompt to the generation endpoint.
      3. Retries up to ``llm.max_retries`` times on transient failures.
      4. On HTTP 401 (token rejected) it clears the token cache and
         retries with a fresh token — this counts as one retry.

    Args:
        user_prompt:   The user's question, pre-formatted with retrieved
                       context chunks inserted before the question text.
        system_prompt: Optional system instruction override.  When None,
                       the value of ``llm.system_prompt`` in app.properties
                       is used.

    Returns:
        The assistant's text content extracted from the LLM response.

    Raises:
        RuntimeError: When all retry attempts are exhausted or a
                      non-retryable HTTP error is received.
    """
    generate_url = props.get("llm.generate_url")
    timeout     = props.get_int("llm.request_timeout_seconds", 120)
    if timeout < 1:
        logger.warning(
            "llm.request_timeout_seconds is %d — invalid. Using default 120s.", timeout
        )
        timeout = 120
    max_retries  = props.get_int("llm.max_retries", 3)                 # safe fallback

    # Guard: max_retries must be at least 1 so the loop runs at least once
    if max_retries < 1:
        max_retries = 1

    # Use the caller-supplied system prompt, or fall back to the configured one
    system = system_prompt or props.get("llm.system_prompt") or (
        "You are a strict enterprise assistant. "
        "Answer ONLY based on the provided context."
    )

    payload    = {"system": system, "user": user_prompt}
    last_error = ""

    for attempt in range(1, max_retries + 1):
        try:
            token = _get_valid_token()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"bearer {token}",
            }

            logger.debug("LLM request attempt %d/%d to %s", attempt, max_retries, generate_url)

            response = requests.post(
                generate_url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )

            # HTTP 401 means the token was rejected server-side.
            # Clear the token cache so the next iteration fetches a new one.
            if response.status_code == 401 and attempt < max_retries:
                global _cached_token
                _cached_token = None
                logger.warning(
                    "LLM returned 401 on attempt %d — clearing token cache and retrying.",
                    attempt,
                )
                continue

            response.raise_for_status()
            data = response.json()

            # Extract 'content' from the expected response shape:
            #   {'response': {'role': 'assistant', 'content': 'text here'}}
            # Fall back to flatter shapes in case the API varies.
            content = (
                data.get("response", {}).get("content")
                or data.get("content")
                or data.get("text")
                or ""
            )

            if not content:
                logger.warning(
                    "LLM response contained no usable content. "
                    "Raw response keys: %s",
                    list(data.keys()),
                )
                return "I received an empty response from the language model."

            return content.strip()

        except RuntimeError as exc:
            # Token fetch or earlier logic raised a clean RuntimeError
            last_error = str(exc)
            logger.warning("LLM attempt %d/%d failed: %s", attempt, max_retries, exc)

        except requests.exceptions.Timeout:
            last_error = "LLM request timed out."
            logger.warning(
                "LLM attempt %d/%d timed out after %ds.",
                attempt, max_retries, timeout,
            )

        except requests.exceptions.HTTPError as exc:
            # 4xx/5xx errors — do not retry (except 401 handled above)
            last_error = f"LLM API HTTP error ({exc.response.status_code})."
            logger.error(
                "LLM HTTP error on attempt %d: %s", attempt, exc
            )
            break

        except Exception as exc:
            last_error = f"Unexpected LLM error: {exc}"
            logger.error(
                "LLM unexpected error on attempt %d: %s", attempt, exc, exc_info=True
            )

    raise RuntimeError(
        f"LLM call failed after {max_retries} attempt(s). "
        f"Last error: {last_error}"
    )
