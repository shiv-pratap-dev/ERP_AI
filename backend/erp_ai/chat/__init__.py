"""Modular 4-stage ERP AI Chatbot package (`backend/erp_ai/chat`).

Public turn entry points are `run_chat_turn` and `stream_chat_turn`
(`orchestrator.py`). Schemas, the normalizer, the cache, the parallel
classifier, and the deterministic tool registry are exported alongside them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from erp_ai.chat.cache import (
    RateLimitExceededError,
    acquire_user_turn_slot,
    freeze_args,
    get_cached_tool_result,
    get_session_slots,
    invalidate_student_cache,
    invalidate_teacher_cache,
    set_cached_tool_result,
    slot_store,
    tool_cache,
    update_session_slots,
    user_guard,
)
from erp_ai.chat.normalizer import (
    COURSE_ALIASES,
    IST,
    MONTHS,
    compute_effective_assignment_status,
    expand_course_aliases,
    extract_assignment_number,
    extract_assignment_title,
    extract_course_code_hint,
    format_ist_datetime,
    has_any_phrase,
    has_phrase,
    match_course_from_list,
    normalize_alnum,
    normalize_chat_text,
    parse_dates,
    parse_datetime_ist,
    parse_iso_or_natural_date,
)
from erp_ai.chat.schemas import (
    ACTIONABLE_ERP_INTENTS,
    FACULTY_INTENT_CATEGORIES,
    NON_TOOL_INTENTS,
    STUDENT_INTENT_CATEGORIES,
    AuthContext,
    ChatResponse,
    ClassificationResult,
    FacultyClassificationOutput,
    IntentCategory,
    SSEActionEvent,
    SSEMetaEvent,
    SSETokenEvent,
    SessionSlotState,
    StudentClassificationOutput,
    TurnSideEffects,
    format_sse,
)

if TYPE_CHECKING:
    from erp_ai.chat.classifier import (
        FACULTY_CLASSIFIER_SYSTEM_PROMPT,
        STUDENT_CLASSIFIER_SYSTEM_PROMPT,
        classify_intents,
    )
    from erp_ai.chat.orchestrator import run_chat_turn, stream_chat_turn
    from erp_ai.chat.tool_registry import (
        FACULTY_CATEGORY_TOOL_MAP,
        MAX_TOOL_ITEMS,
        STUDENT_CATEGORY_TOOL_MAP,
        get_tool_names_for_intents,
        get_tools_for_intents,
    )

_LAZY_TOOL_REGISTRY_EXPORTS = {
    "FACULTY_CATEGORY_TOOL_MAP",
    "MAX_TOOL_ITEMS",
    "STUDENT_CATEGORY_TOOL_MAP",
    "get_tool_names_for_intents",
    "get_tools_for_intents",
}

_LAZY_CLASSIFIER_EXPORTS = {
    "FACULTY_CLASSIFIER_SYSTEM_PROMPT",
    "STUDENT_CLASSIFIER_SYSTEM_PROMPT",
    "classify_intents",
}

_LAZY_ORCHESTRATOR_EXPORTS = {
    "run_chat_turn",
    "stream_chat_turn",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_TOOL_REGISTRY_EXPORTS:
        from erp_ai.chat import tool_registry

        return getattr(tool_registry, name)
    if name in _LAZY_CLASSIFIER_EXPORTS:
        from erp_ai.chat import classifier

        return getattr(classifier, name)
    if name in _LAZY_ORCHESTRATOR_EXPORTS:
        from erp_ai.chat import orchestrator

        return getattr(orchestrator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ACTIONABLE_ERP_INTENTS",
    "AuthContext",
    "COURSE_ALIASES",
    "ChatResponse",
    "ClassificationResult",
    "FACULTY_CATEGORY_TOOL_MAP",
    "FACULTY_CLASSIFIER_SYSTEM_PROMPT",
    "FACULTY_INTENT_CATEGORIES",
    "FacultyClassificationOutput",
    "IST",
    "IntentCategory",
    "MAX_TOOL_ITEMS",
    "MONTHS",
    "NON_TOOL_INTENTS",
    "RateLimitExceededError",
    "SSEActionEvent",
    "SSEMetaEvent",
    "SSETokenEvent",
    "STUDENT_CATEGORY_TOOL_MAP",
    "STUDENT_CLASSIFIER_SYSTEM_PROMPT",
    "STUDENT_INTENT_CATEGORIES",
    "SessionSlotState",
    "StudentClassificationOutput",
    "TurnSideEffects",
    "acquire_user_turn_slot",
    "classify_intents",
    "compute_effective_assignment_status",
    "expand_course_aliases",
    "extract_assignment_number",
    "extract_assignment_title",
    "extract_course_code_hint",
    "format_ist_datetime",
    "format_sse",
    "freeze_args",
    "get_cached_tool_result",
    "get_session_slots",
    "get_tool_names_for_intents",
    "get_tools_for_intents",
    "has_any_phrase",
    "has_phrase",
    "invalidate_student_cache",
    "invalidate_teacher_cache",
    "match_course_from_list",
    "normalize_alnum",
    "normalize_chat_text",
    "run_chat_turn",
    "stream_chat_turn",
    "parse_dates",
    "parse_datetime_ist",
    "parse_iso_or_natural_date",
    "set_cached_tool_result",
    "slot_store",
    "tool_cache",
    "update_session_slots",
    "user_guard",
]
