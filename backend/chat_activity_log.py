"""Terminal activity log for each ERP chatbot turn.

Shows, in order, for every user query:
1. User query
2. Thinking / routing (including parallel `all_classified_intents`, resolved
   tools, model thoughts, and per-model errors)
3. Tools called (thread-safe across parallel `asyncio.gather` executions)
4. Tool responses
5. Final response

`chat_turn(...)` works as both an `@asynccontextmanager` (`async with chat_turn(...)`)
and a synchronous context manager (`with chat_turn(...)`), with defensive
`ContextVar` token reset handling so streaming generators never crash with
`ValueError: Token was created in a different Context`.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import textwrap
import threading
import traceback
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from datetime import datetime
from functools import wraps
from typing import Any, AsyncIterator


_CURRENT: ContextVar["ChatTurnLog | None"] = ContextVar(
    "erp_chat_turn_log",
    default=None,
)
_PARALLEL_BATCH: ContextVar[int | None] = ContextVar(
    "erp_chat_parallel_batch",
    default=None,
)

_MAX_TOOL_CHARS = int(os.getenv("CHAT_LOG_TOOL_CHARS", "1800"))
_MAX_FINAL_CHARS = int(os.getenv("CHAT_LOG_FINAL_CHARS", "4000"))
_ENABLED = os.getenv("CHAT_ACTIVITY_LOG", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}

_HTTP_STATUS_RE = re.compile(r"\b(400|401|403|404|408|429|500|502|503|504)\b")


def get_turn_log() -> "ChatTurnLog | None":
    return _CURRENT.get()


def current_parallel_batch() -> int | None:
    """Batch size while the orchestrator is running tool calls concurrently."""
    return _PARALLEL_BATCH.get()


def set_parallel_batch(size: int | None) -> Token[int | None]:
    """Mark the current task as running `size` tool calls at once."""
    return _PARALLEL_BATCH.set(size if size and size > 1 else None)


def reset_parallel_batch(token: Token[int | None]) -> None:
    try:
        _PARALLEL_BATCH.reset(token)
    except ValueError:
        try:
            _PARALLEL_BATCH.set(None)
        except Exception:
            pass


def _is_quiet_exit(exc: BaseException) -> bool:
    """Client disconnects are not turn failures."""
    return isinstance(exc, (GeneratorExit, asyncio.CancelledError))


def _safe_reset_contextvar(token: Token["ChatTurnLog | None"]) -> None:
    """Reset `_CURRENT` safely even if a streaming generator hopped contexts."""
    try:
        _CURRENT.reset(token)
    except ValueError:
        try:
            _CURRENT.set(None)
        except Exception:
            pass


def _extract_status_code(exc: BaseException) -> int | str | None:
    for attr in ("status_code", "code", "http_status", "status"):
        val = getattr(exc, attr, None)
        if isinstance(val, (int, str)) and str(val).strip():
            return val
    response = getattr(exc, "response", None)
    if response is not None:
        resp_status = getattr(response, "status_code", None)
        if isinstance(resp_status, int):
            return resp_status
    match = _HTTP_STATUS_RE.search(str(exc))
    if match:
        return int(match.group(1))
    return None


def _clip(value: Any, limit: int) -> str:
    if value is None:
        return "null"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str, ensure_ascii=False, indent=2)
    else:
        text = str(value)
    text = text.replace("\r\n", "\n").strip()
    if len(text) > limit:
        return text[: limit - 20] + "\n... [truncated] ..."
    return text


def _indent(text: str, prefix: str = "   ") -> str:
    return textwrap.indent(text, prefix)


class ChatTurnLog:
    """Collects one chatbot turn and prints a structured terminal report."""

    def __init__(
        self,
        *,
        query: str,
        role: str = "student",
        user_label: str | None = None,
        student_id: str | None = None,
        teacher_id: str | None = None,
        semester: int | None = None,
    ) -> None:
        self.query = query
        self.role = role
        self.user_label = user_label or "unknown"
        self.student_id = student_id
        self.teacher_id = teacher_id
        self.semester = semester
        self.started_at = datetime.now()
        self._lock = threading.RLock()

        self.all_classified_intents: list[str] = []
        self.resolved_tool_names: list[str] = []
        self._thinking: list[str] = []
        self._thoughts: list[str] = []
        self._tools: list[dict[str, Any]] = []
        self._model_errors: list[dict[str, Any]] = []
        self._final: str | None = None
        self._final_meta: dict[str, Any] = {}
        self._error: str | None = None
        self._printed = False

        self._banner_start()

    def _print(self, text: str = "") -> None:
        print(text, flush=True)

    def _banner_start(self) -> None:
        if not _ENABLED:
            return
        with self._lock:
            stamp = self.started_at.strftime("%Y-%m-%d %H:%M:%S")
            self._print("")
            self._print("=" * 78)
            self._print(f"ERP CHAT TURN  {stamp}")
            self._print("-" * 78)
            self._print(f"User     : {self.user_label}  ({self.role})")
            if self.student_id:
                self._print(f"Student  : {self.student_id}")
            if self.teacher_id:
                self._print(f"Teacher  : {self.teacher_id}")
            if self.semester is not None:
                self._print(f"Semester : {self.semester}")
            self._print("-" * 78)
            self._print("1. USER QUERY")
            self._print(_indent(self.query or "(empty)"))
            self._print("-" * 78)
            self._print("2. THINKING / ROUTING")

    def thinking(self, note: str) -> None:
        note = (note or "").strip()
        if not note:
            return
        with self._lock:
            self._thinking.append(note)
            if _ENABLED:
                self._print(_indent(f"• {note}"))

    def classified_intents(
        self,
        intents: list[Any],
        *,
        source: str = "lexical_probes",
        elapsed_ms: float | None = None,
    ) -> None:
        canonical = [
            str(item.value if hasattr(item, "value") else item)
            for item in intents
        ]
        with self._lock:
            self.all_classified_intents = canonical
            timing = f", {elapsed_ms:.1f} ms" if elapsed_ms is not None else ""
            msg = (
                f"all_classified_intents={canonical} "
                f"(source={source}{timing})"
            )
            self._thinking.append(msg)
            if _ENABLED:
                self._print(_indent(f"• {msg}"))

    def resolved_tools(self, tool_names: list[str]) -> None:
        names = [str(name) for name in tool_names]
        with self._lock:
            self.resolved_tool_names = names
            msg = f"resolved_tools={names}"
            self._thinking.append(msg)
            if _ENABLED:
                self._print(_indent(f"• {msg}"))

    def model_thought(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            self._thoughts.append(text)
            if _ENABLED:
                self._print(_indent("• [model thought]"))
                self._print(_indent(_clip(text, 1200), "     "))

    def model_error(
        self,
        model_name: str,
        exc: BaseException,
        *,
        attempt: int | None = None,
        status_code: int | str | None = None,
    ) -> None:
        resolved_status = (
            status_code if status_code is not None else _extract_status_code(exc)
        )
        record = {
            "model": model_name,
            "attempt": attempt,
            "status_code": resolved_status,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
        with self._lock:
            self._model_errors.append(record)
            if _ENABLED:
                attempt_str = f" attempt={attempt}" if attempt is not None else ""
                status_str = (
                    f" status={resolved_status}"
                    if resolved_status is not None
                    else ""
                )
                self._print(
                    _indent(
                        f"• [model error] model={model_name}"
                        f"{attempt_str}{status_str} "
                        f"{type(exc).__name__}: {_clip(str(exc), 400)}"
                    )
                )

    def tool_call(
        self,
        name: str,
        args: Any = None,
        *,
        call_id: str | None = None,
        parallel_batch: int | None = None,
    ) -> None:
        entry = {
            "name": name,
            "call_id": call_id,
            "args": args,
            "response": None,
            "parallel_batch": parallel_batch,
        }
        with self._lock:
            self._tools.append(entry)
            if _ENABLED:
                self._print("-" * 78)
                batch_tag = (
                    f" [parallel x{parallel_batch}]"
                    if parallel_batch and parallel_batch > 1
                    else ""
                )
                self._print(f"3. TOOL CALL{batch_tag}")
                self._print(_indent(f"→ {name}"))
                if args is not None:
                    self._print(_indent("args:"))
                    self._print(_indent(_clip(args, 800), "     "))

    def tool_response(
        self,
        name: str,
        response: Any,
        *,
        call_id: str | None = None,
        cached: bool = False,
        elapsed_ms: float | None = None,
    ) -> None:
        with self._lock:
            matched = False
            if call_id is not None:
                for entry in self._tools:
                    if entry.get("call_id") == call_id and entry["response"] is None:
                        entry["response"] = response
                        entry["cached"] = cached
                        matched = True
                        break
            if not matched:
                for entry in self._tools:
                    if entry["name"] == name and entry["response"] is None:
                        entry["response"] = response
                        entry["cached"] = cached
                        matched = True
                        break
            if not matched:
                self._tools.append(
                    {
                        "name": name,
                        "call_id": call_id,
                        "args": None,
                        "response": response,
                        "cached": cached,
                    }
                )

            if _ENABLED:
                tags: list[str] = []
                if cached:
                    tags.append("cache=HIT")
                if elapsed_ms is not None:
                    tags.append(f"{elapsed_ms:.1f} ms")
                suffix = f" ({', '.join(tags)})" if tags else ""
                self._print("4. TOOL RESPONSE")
                self._print(_indent(f"← {name}{suffix}"))
                self._print(_indent(_clip(response, _MAX_TOOL_CHARS), "     "))

    def final_response(
        self,
        text: str,
        *,
        kind: str | None = None,
        title: str | None = None,
        source: str | None = None,
    ) -> None:
        with self._lock:
            self._final = text or ""
            self._final_meta = {
                "kind": kind,
                "title": title,
                "source": source,
            }
            self._print_closing()

    def error(self, exc: BaseException) -> None:
        with self._lock:
            self._error = f"{type(exc).__name__}: {exc}"
            if _ENABLED:
                self._print("-" * 78)
                self._print("ERROR")
                self._print(_indent(self._error))
                self._print(_indent(traceback.format_exc(limit=4)))

    def _print_closing(self) -> None:
        if self._printed or not _ENABLED:
            self._printed = True
            return
        self._printed = True

        if not self._tools:
            self._print("-" * 78)
            self._print("3. TOOLS CALLED")
            self._print(_indent("(none)"))
            self._print("-" * 78)
            self._print("4. TOOL RESPONSES")
            self._print(_indent("(none)"))

        self._print("-" * 78)
        self._print("5. FINAL RESPONSE")
        meta_bits = [
            f"{key}={value}"
            for key, value in self._final_meta.items()
            if value
        ]
        if meta_bits:
            self._print(_indent("[" + ", ".join(meta_bits) + "]"))
        self._print(_indent(_clip(self._final or "(empty)", _MAX_FINAL_CHARS)))

        elapsed_ms = int(
            (datetime.now() - self.started_at).total_seconds() * 1000
        )
        summary_parts = [
            f"Done in {elapsed_ms} ms",
            f"intents={self.all_classified_intents}",
            f"tools={len(self._tools)}",
            f"thoughts={len(self._thoughts)}",
        ]
        if self._model_errors:
            summary_parts.append(f"model_errors={len(self._model_errors)}")
        self._print("-" * 78)
        self._print(" | ".join(summary_parts))
        self._print("=" * 78)
        self._print("")


@asynccontextmanager
async def async_chat_turn(
    *,
    query: str,
    role: str = "student",
    user_label: str | None = None,
    student_id: str | None = None,
    teacher_id: str | None = None,
    semester: int | None = None,
) -> AsyncIterator[ChatTurnLog]:
    """Async-safe `@asynccontextmanager` for FastAPI async chat endpoints."""
    log = ChatTurnLog(
        query=query,
        role=role,
        user_label=user_label,
        student_id=student_id,
        teacher_id=teacher_id,
        semester=semester,
    )
    token = _CURRENT.set(log)
    try:
        yield log
    except BaseException as exc:
        if _is_quiet_exit(exc):
            if not log._printed:
                log.final_response(
                    "Generation stopped.",
                    kind="stopped",
                    source="client_disconnect",
                )
        else:
            log.error(exc)
            if not log._printed:
                log.final_response(
                    f"Turn failed: {type(exc).__name__}: {exc}",
                    kind="error",
                    source="exception",
                )
        raise
    finally:
        if not log._printed:
            log.final_response(
                "(no final response recorded)",
                kind="incomplete",
                source="chat_turn",
            )
        _safe_reset_contextvar(token)


class _DualChatTurnContext:
    """Supports both `async with chat_turn(...)` and `with chat_turn(...)`."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._acm: Any = None
        self._log: ChatTurnLog | None = None
        self._token: Token[ChatTurnLog | None] | None = None

    async def __aenter__(self) -> ChatTurnLog:
        self._acm = async_chat_turn(**self._kwargs)
        return await self._acm.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if self._acm is not None:
            return bool(await self._acm.__aexit__(exc_type, exc_val, exc_tb))
        return False

    def __enter__(self) -> ChatTurnLog:
        self._log = ChatTurnLog(**self._kwargs)
        self._token = _CURRENT.set(self._log)
        return self._log

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        log = self._log
        token = self._token
        try:
            if log is not None:
                if exc_val is not None and _is_quiet_exit(exc_val):
                    if not log._printed:
                        log.final_response(
                            "Generation stopped.",
                            kind="stopped",
                            source="client_disconnect",
                        )
                elif exc_val is not None:
                    log.error(exc_val)
                    if not log._printed:
                        log.final_response(
                            f"Turn failed: {type(exc_val).__name__}: {exc_val}",
                            kind="error",
                            source="exception",
                        )
                elif not log._printed:
                    log.final_response(
                        "(no final response recorded)",
                        kind="incomplete",
                        source="chat_turn",
                    )
        finally:
            if token is not None:
                _safe_reset_contextvar(token)
        return False


def chat_turn(
    *,
    query: str,
    role: str = "student",
    user_label: str | None = None,
    student_id: str | None = None,
    teacher_id: str | None = None,
    semester: int | None = None,
) -> _DualChatTurnContext:
    """Context manager usable with both `async with` and `with`."""
    return _DualChatTurnContext(
        query=query,
        role=role,
        user_label=user_label,
        student_id=student_id,
        teacher_id=teacher_id,
        semester=semester,
    )


def _summarize_call_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any] | None:
    brief_args: dict[str, Any] = {}
    if args:
        brief_args["args"] = [
            _clip(arg, 120) if not isinstance(arg, (str, int, float, bool)) else arg
            for arg in args[:4]
        ]
    if kwargs:
        brief_args["kwargs"] = {
            key: (
                _clip(value, 120)
                if not isinstance(value, (str, int, float, bool, type(None)))
                else value
            )
            for key, value in list(kwargs.items())[:8]
        }
    return brief_args or None


def traced(name: str | None = None):
    """Decorator: log sync or async function call + response on active chat turn."""

    def decorator(fn):
        tool_name = name or fn.__name__

        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                log = get_turn_log()
                if log:
                    log.tool_call(tool_name, _summarize_call_args(args, kwargs))
                result = await fn(*args, **kwargs)
                if log:
                    log.tool_response(tool_name, result)
                return result

            return async_wrapper

        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            log = get_turn_log()
            if log:
                log.tool_call(tool_name, _summarize_call_args(args, kwargs))
            result = fn(*args, **kwargs)
            if log:
                log.tool_response(tool_name, result)
            return result

        return sync_wrapper

    return decorator
