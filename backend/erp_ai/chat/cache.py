"""In-memory short-TTL tool cache, 5-min user slot memory, and concurrency guard.

1. Read-only tool cache: 30s TTL keyed by `(user_key, tool_name, frozen_args)`,
   with targeted invalidation on student upload and faculty assignment creation.
2. Session slot store: 5-minute TTL per user storing `last_intents`,
   `last_course_code`, `last_course_name`, and `last_assignment_id`.
3. Per-user concurrency & rate guard: async semaphore per `(event_loop, user_key)`
   plus sliding-window per-minute request cap.
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
import copy
import json
import os
import threading
import time
from typing import Any, AsyncIterator
import weakref

from erp_ai.chat.schemas import IntentCategory, SessionSlotState

READ_CACHE_TTL_SECONDS = float(os.getenv("CHAT_TOOL_CACHE_TTL", "30"))
SLOT_MEMORY_TTL_SECONDS = float(os.getenv("CHAT_SLOT_TTL", "300"))
USER_MAX_CONCURRENCY = max(1, int(os.getenv("CHAT_USER_MAX_CONCURRENCY", "2")))
USER_RATE_LIMIT_PER_MIN = max(1, int(os.getenv("CHAT_USER_RATE_LIMIT_PER_MIN", "30")))
_MAX_CACHE_ENTRIES = 1024


class RateLimitExceededError(RuntimeError):
    """Raised when a single user exceeds per-minute or concurrency limits."""


def freeze_args(args: Any = None, kwargs: Any = None) -> str:
    """Deterministically serialize tool arguments into a hashable string key."""
    return json.dumps(
        {
            "args": list(args) if isinstance(args, (list, tuple)) else args,
            "kwargs": kwargs or {},
        },
        sort_keys=True,
        default=str,
        ensure_ascii=False,
    )


class ToolResultCache:
    """Thread-safe 30-second read-only tool output cache."""

    def __init__(self, default_ttl: float = READ_CACHE_TTL_SECONDS) -> None:
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        # key: (user_key, tool_name, frozen_args) -> (expires_at_monotonic, payload)
        self._store: dict[tuple[str, str, str], tuple[float, Any]] = {}

    def _evict_expired_unlocked(self, now: float) -> None:
        expired = [key for key, (exp, _val) in self._store.items() if exp <= now]
        for key in expired:
            self._store.pop(key, None)
        if len(self._store) > _MAX_CACHE_ENTRIES:
            oldest = sorted(self._store.items(), key=lambda kv: kv[1][0])[
                : len(self._store) - _MAX_CACHE_ENTRIES
            ]
            for key, _ in oldest:
                self._store.pop(key, None)

    def get(
        self,
        user_key: str,
        tool_name: str,
        frozen_args: str,
    ) -> tuple[bool, Any]:
        now = time.monotonic()
        cache_key = (user_key, tool_name, frozen_args)
        with self._lock:
            entry = self._store.get(cache_key)
            if entry is None:
                return False, None
            expires_at, value = entry
            if expires_at <= now:
                self._store.pop(cache_key, None)
                return False, None
            return True, copy.deepcopy(value)

    def set(
        self,
        user_key: str,
        tool_name: str,
        frozen_args: str,
        value: Any,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        if ttl <= 0:
            return
        now = time.monotonic()
        cache_key = (user_key, tool_name, frozen_args)
        with self._lock:
            self._evict_expired_unlocked(now)
            self._store[cache_key] = (now + ttl, copy.deepcopy(value))

    def invalidate_user(
        self,
        user_key: str,
        *,
        tool_names: set[str] | None = None,
    ) -> int:
        removed = 0
        with self._lock:
            keys = [
                key
                for key in self._store
                if key[0] == user_key
                and (tool_names is None or key[1] in tool_names)
            ]
            for key in keys:
                self._store.pop(key, None)
                removed += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class SessionSlotStore:
    """Thread-safe 5-minute user conversational slot memory."""

    def __init__(self, ttl_seconds: float = SLOT_MEMORY_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._slots: dict[str, tuple[float, SessionSlotState]] = {}

    def get(self, user_key: str) -> SessionSlotState:
        now = time.monotonic()
        with self._lock:
            entry = self._slots.get(user_key)
            if entry is None:
                return SessionSlotState()
            expires_at, state = entry
            if expires_at <= now:
                self._slots.pop(user_key, None)
                return SessionSlotState()
            return state.model_copy(deep=True)

    def update(
        self,
        user_key: str,
        *,
        intents: list[IntentCategory] | None = None,
        course_code: str | None = None,
        course_name: str | None = None,
        assignment_id: str | None = None,
    ) -> SessionSlotState:
        now = time.monotonic()
        with self._lock:
            expired = [k for k, (exp, _s) in self._slots.items() if exp <= now]
            for k in expired:
                self._slots.pop(k, None)

            existing = self._slots.get(user_key)
            current = (
                existing[1].model_copy(deep=True)
                if existing is not None and existing[0] > now
                else SessionSlotState()
            )
            if intents is not None:
                current.last_intents = list(intents)
            if course_code is not None:
                current.last_course_code = course_code
                if course_name is not None:
                    current.last_course_name = course_name
            elif course_name is not None:
                current.last_course_name = course_name
            if assignment_id is not None:
                current.last_assignment_id = assignment_id
            current.updated_at = time.time()

            self._slots[user_key] = (now + self._ttl, current)
            return current.model_copy(deep=True)

    def clear(self, user_key: str | None = None) -> None:
        with self._lock:
            if user_key is None:
                self._slots.clear()
            else:
                self._slots.pop(user_key, None)


class UserConcurrencyGuard:
    """Per-user async concurrency semaphore + per-minute rate limiter."""

    def __init__(
        self,
        *,
        max_concurrent: int = USER_MAX_CONCURRENCY,
        rate_limit_per_min: int = USER_RATE_LIMIT_PER_MIN,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._rate_limit_per_min = rate_limit_per_min
        self._lock = threading.Lock()
        # WeakKeyDictionary indexed by event loop so multiple `asyncio.run(...)`
        # loops in tests never share loop-bound Semaphore instances.
        self._loop_semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]
        ] = weakref.WeakKeyDictionary()
        self._request_timestamps: dict[str, deque[float]] = {}

    def _get_semaphore(self, user_key: str) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        with self._lock:
            per_loop = self._loop_semaphores.get(loop)
            if per_loop is None:
                per_loop = {}
                self._loop_semaphores[loop] = per_loop
            sem = per_loop.get(user_key)
            if sem is None:
                sem = asyncio.Semaphore(self._max_concurrent)
                per_loop[user_key] = sem
            return sem

    def _check_rate_limit(self, user_key: str) -> None:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            window = self._request_timestamps.get(user_key)
            if window is None:
                window = deque()
                self._request_timestamps[user_key] = window
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self._rate_limit_per_min:
                raise RateLimitExceededError(
                    "Too many requests in a short window. Please wait a few seconds and retry."
                )
            window.append(now)

    @asynccontextmanager
    async def acquire(
        self,
        user_key: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> AsyncIterator[None]:
        self._check_rate_limit(user_key)
        sem = self._get_semaphore(user_key)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise RateLimitExceededError(
                "Another assistant request is still running for your account. Please wait a moment."
            ) from exc
        try:
            yield
        finally:
            sem.release()


# Module-level singletons used by `tool_registry.py`, `orchestrator.py`, and APIs
tool_cache = ToolResultCache()
slot_store = SessionSlotStore()
user_guard = UserConcurrencyGuard()


def get_cached_tool_result(
    user_key: str,
    tool_name: str,
    args: Any = None,
    kwargs: Any = None,
) -> tuple[bool, Any]:
    return tool_cache.get(user_key, tool_name, freeze_args(args, kwargs))


def set_cached_tool_result(
    user_key: str,
    tool_name: str,
    value: Any,
    args: Any = None,
    kwargs: Any = None,
    *,
    ttl_seconds: float | None = None,
) -> None:
    tool_cache.set(
        user_key,
        tool_name,
        freeze_args(args, kwargs),
        value,
        ttl_seconds=ttl_seconds,
    )


def invalidate_student_cache(student_id: str, user_id: str | None = None) -> int:
    """Invalidate cached read-only tool results for a student (e.g. on file upload)."""
    removed = tool_cache.invalidate_user(f"student:{student_id}")
    if user_id:
        removed += tool_cache.invalidate_user(f"user:{user_id}")
    return removed


def invalidate_teacher_cache(teacher_id: str, user_id: str | None = None) -> int:
    """Invalidate cached read-only tool results for a teacher (e.g. on create assignment)."""
    removed = tool_cache.invalidate_user(f"teacher:{teacher_id}")
    if user_id:
        removed += tool_cache.invalidate_user(f"user:{user_id}")
    return removed


def get_session_slots(user_key: str) -> SessionSlotState:
    return slot_store.get(user_key)


def update_session_slots(
    user_key: str,
    *,
    intents: list[IntentCategory] | None = None,
    course_code: str | None = None,
    course_name: str | None = None,
    assignment_id: str | None = None,
) -> SessionSlotState:
    return slot_store.update(
        user_key,
        intents=intents,
        course_code=course_code,
        course_name=course_name,
        assignment_id=assignment_id,
    )


@asynccontextmanager
async def acquire_user_turn_slot(
    user_key: str,
    *,
    timeout_seconds: float = 15.0,
) -> AsyncIterator[None]:
    async with user_guard.acquire(user_key, timeout_seconds=timeout_seconds):
        yield
