"""
app/api/routes/chat.py
----------------------
Chat and RAG pipeline endpoints.

POST /api/v1/chat/query
    Standard RAG query — returns answer + sources.
    Used by the custom admin UI or direct API clients.

POST /api/v1/chat/completions
    OpenAI-compatible chat completions endpoint.
    Open WebUI is configured to call THIS endpoint as its model backend.
    This is how the RAG pipeline is exposed to Open WebUI without any
    model configuration inside Open WebUI itself.

POST /api/v1/chat/sessions
    Create a new chat session.

GET  /api/v1/chat/sessions/{id}/messages
    Retrieve messages for a session.

POST /api/v1/chat/upload
    Upload a file to use as in-session context (not persisted to vector store).
"""

import logging
import time
from typing import Any

from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File, status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.core.dependencies import get_current_user, UserContext
from app.core.rag_pipeline import answer_query
from app.db.chat_repository import (
    create_chat_session, save_exchange, get_session_messages,
)
from app.sources.document_loaders import load_uploaded_file

logger = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None
    uploaded_file_texts: list[str] | None = None


# OpenAI-compatible message shape
class OAIMessage(BaseModel):
    role: str   # "user" | "assistant" | "system"
    content: str


class OAICompletionRequest(BaseModel):
    model: str = "enterprise-rag"
    messages: list[OAIMessage]
    stream: bool = False
    temperature: float = 0.7    # accepted but ignored — RAG pipeline is deterministic
    max_tokens: int | None = None


# ---------------------------------------------------------------------------
# Standard RAG query
# ---------------------------------------------------------------------------

@router.post("/query", summary="RAG query — returns answer and source citations")
async def rag_query(
    body: QueryRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    Run the full RAG pipeline for the given query.

    Returns:
        response      — the LLM answer
        sources       — list of retrieved chunks with metadata
        has_context   — whether any relevant context was found
        elapsed_ms    — end-to-end processing time in milliseconds
    """
    start = time.time()

    result = answer_query(
        user_query=body.query,
        user_id=user["user_id"],
        uploaded_file_texts=body.uploaded_file_texts,
    )

    elapsed_ms = int((time.time() - start) * 1000)

    # Persist exchange if session_id provided
    if body.session_id:
        source_ids = list(
            {c.get("source_id") for c in result.get("sources", []) if c.get("source_id")}
        )
        save_exchange(
            session_id=body.session_id,
            user_query=body.query,
            assistant_response=result["response"],
            source_ids=source_ids or None,
        )

    return {
        "response":    result["response"],
        "sources":     result.get("sources", []),
        "has_context": result.get("has_context", False),
        "error":       result.get("error"),
        "elapsed_ms":  elapsed_ms,
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible endpoint  (Open WebUI calls this)
# ---------------------------------------------------------------------------

@router.post("/completions", summary="OpenAI-compatible endpoint for Open WebUI")
async def completions(
    body: OAICompletionRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    OpenAI Chat Completions-compatible endpoint.

    Open WebUI is configured with:
        API Base URL : http://backend:8000/api/v1/chat
        Model        : enterprise-rag
        API Key      : (the user's JWT, injected by Nginx)

    The last 'user' message in the conversation is used as the RAG query.
    Previous messages are used to build a simple conversation history prefix
    so follow-up questions have context.
    """
    # Extract the last user message as the primary query
    user_messages = [m for m in body.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="No user message found in request.")

    user_query = user_messages[-1].content

    # Build conversation context from prior turns if available
    history_prefix = ""
    prior_turns = body.messages[:-1]
    if prior_turns:
        history_parts = []
        for m in prior_turns[-6:]:   # last 3 turns (6 messages)
            prefix = "User" if m.role == "user" else "Assistant"
            history_parts.append(f"{prefix}: {m.content}")
        if history_parts:
            history_prefix = "Previous conversation:\n" + "\n".join(history_parts) + "\n\n"

    full_query = history_prefix + user_query if history_prefix else user_query

    result = answer_query(
        user_query=full_query,
        user_id=user["user_id"],
    )

    answer = result["response"]
    created_ts = int(time.time())

    # --- Streaming (Server-Sent Events) ---
    if body.stream:
        async def _stream():
            # Yield as a single chunk — true token streaming requires
            # an LLM that supports it; this wraps the complete answer.
            chunk = {
                "id": f"chatcmpl-rag-{created_ts}",
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": body.model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": answer},
                    "finish_reason": None,
                }],
            }
            import json
            yield f"data: {json.dumps(chunk)}\n\n"
            # Final chunk with finish_reason
            done_chunk = {
                "id": f"chatcmpl-rag-{created_ts}",
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": body.model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }],
            }
            yield f"data: {json.dumps(done_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_stream(), media_type="text/event-stream")

    # --- Non-streaming ---
    return {
        "id":      f"chatcmpl-rag-{created_ts}",
        "object":  "chat.completion",
        "created": created_ts,
        "model":   body.model,
        "choices": [{
            "index":         0,
            "message":       {"role": "assistant", "content": answer},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens":     len(full_query.split()),
            "completion_tokens": len(answer.split()),
            "total_tokens":      len(full_query.split()) + len(answer.split()),
        },
    }


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

@router.post("/sessions", status_code=201, summary="Create a new chat session")
async def new_session(
    user: UserContext = Depends(get_current_user),
):
    """Creates a new chat session in SQL Server and returns the session_id UUID."""
    sid = create_chat_session(user_id=user["user_id"])
    if not sid:
        raise HTTPException(status_code=500, detail="Could not create chat session.")
    return {"session_id": sid}


@router.get("/sessions/{session_id}/messages", summary="Get session message history")
async def get_messages(
    session_id: str,
    user: UserContext = Depends(get_current_user),
):
    """Returns all messages for the given session ordered by creation time."""
    messages = get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}


# ---------------------------------------------------------------------------
# In-session file upload
# ---------------------------------------------------------------------------

@router.post("/upload", summary="Upload a file for in-session context")
async def upload_chat_file(
    file: UploadFile = File(...),
    user: UserContext = Depends(get_current_user),
):
    """
    Extracts text from an uploaded file and returns it as a list of segments.
    The client includes these segments in subsequent /query requests via
    the uploaded_file_texts field.  Files are NOT persisted to disk.
    """
    from app.utils.properties_loader import props

    max_mb = props.get_int("upload.max_file_size_mb", 50)
    content = await file.read()

    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {max_mb} MB limit.",
        )

    texts = load_uploaded_file(content, file.filename or "upload")
    if not texts:
        raise HTTPException(
            status_code=422,
            detail="Could not extract text from the uploaded file.",
        )

    return {
        "filename": file.filename,
        "segments": len(texts),
        "texts": texts,
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible model list  (Open WebUI discovery)
# ---------------------------------------------------------------------------

@router.get("/models", summary="List available models (OpenAI-compatible)")
async def list_models(user: UserContext = Depends(get_current_user)):
    """
    Returns the model list in OpenAI format.
    Open WebUI calls GET /models to discover available models.
    """
    from app.utils.properties_loader import props
    model_name = props.get("openwebui.model_name", "Enterprise RAG")
    return {
        "object": "list",
        "data": [
            {
                "id":       "enterprise-rag",
                "object":   "model",
                "created":  1700000000,
                "owned_by": "enterprise",
                "name":     model_name,
            }
        ],
    }
