"""Thin async FastAPI host for the ERP assistant.

`POST /api/assistant/chat` and `POST /api/assistant/chat/stream` authenticate
the JWT cookie, build an `AuthContext`, and delegate the turn to
`erp_ai.chat.orchestrator`. Rule routers and fake text chunking are gone.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from database import get_connection
from erp_ai.chat.cache import RateLimitExceededError
from erp_ai.chat.orchestrator import run_chat_turn, stream_chat_turn
from erp_ai.chat.schemas import AuthContext

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    message: str


def _clean_message(message: str) -> str:
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    return text


def _load_auth_sync(access_token: str | None) -> AuthContext:
    """Resolve the JWT cookie to a student or teacher `AuthContext`."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    from main import verify_token

    user = verify_token(access_token)
    role = user.get("role")
    user_id = str(user["id"])
    email = user.get("email")

    if role == "teacher":
        from faculty_api import _require_teacher

        teacher, teacher_id = _require_teacher(access_token)
        return AuthContext(
            user_id=user_id,
            role="teacher",
            email=teacher.get("email") or email,
            teacher_id=str(teacher_id),
            teacher=teacher,
        )

    if role != "student":
        raise HTTPException(
            status_code=403,
            detail="Only students and teachers can use the assistant",
        )

    with get_connection() as conn:
        student = conn.execute(
            "SELECT id, semester FROM students WHERE user_id = %s",
            (user_id,),
        ).fetchone()

    if not student or student[1] is None:
        raise HTTPException(status_code=404, detail="Student profile not found")

    return AuthContext(
        user_id=user_id,
        role="student",
        email=email,
        student_id=str(student[0]),
        semester=int(student[1]),
    )


async def _load_auth(access_token: str | None) -> AuthContext:
    return await asyncio.to_thread(_load_auth_sync, access_token)


@router.post("/chat")
async def chat(
    data: ChatRequest,
    access_token: str | None = Cookie(default=None),
):
    message = _clean_message(data.message)
    auth_ctx = await _load_auth(access_token)
    try:
        return await run_chat_turn(message, auth_ctx)
    except RateLimitExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.post("/chat/stream")
async def chat_stream(
    data: ChatRequest,
    request: Request,
    access_token: str | None = Cookie(default=None),
):
    """SSE stream. Static replies are one chunk; model answers are real tokens."""
    message = _clean_message(data.message)
    auth_ctx = await _load_auth(access_token)
    return StreamingResponse(
        stream_chat_turn(message, auth_ctx, request=request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
