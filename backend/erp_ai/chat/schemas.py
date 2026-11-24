"""Canonical schemas, enums, auth/session state, and SSE contracts for ERP chat."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class IntentCategory(str, Enum):
    """Canonical intent categories produced by Stage 1 parallel classification."""

    # Student ERP domain categories
    LMS = "lms"
    ACADEMIC = "academic"
    EXAMINATION = "examination"
    PERSONAL_INFO = "personal_info"

    # Faculty ERP domain categories
    FACULTY_LMS_READ = "faculty_lms_read"
    FACULTY_LMS_WRITE = "faculty_lms_write"
    FACULTY_HR_PAYROLL = "faculty_hr_payroll"
    FACULTY_NAVIGATION = "faculty_navigation"

    # Shared cross-role categories
    PLANNING = "planning"
    GREETING_HELP = "greeting_help"
    FORBIDDEN_WRITE = "forbidden_write"
    OUT_OF_SCOPE = "out_of_scope"


STUDENT_INTENT_CATEGORIES: tuple[IntentCategory, ...] = (
    IntentCategory.LMS,
    IntentCategory.ACADEMIC,
    IntentCategory.EXAMINATION,
    IntentCategory.PERSONAL_INFO,
    IntentCategory.PLANNING,
    IntentCategory.GREETING_HELP,
    IntentCategory.FORBIDDEN_WRITE,
    IntentCategory.OUT_OF_SCOPE,
)

FACULTY_INTENT_CATEGORIES: tuple[IntentCategory, ...] = (
    IntentCategory.FACULTY_LMS_READ,
    IntentCategory.FACULTY_LMS_WRITE,
    IntentCategory.FACULTY_HR_PAYROLL,
    IntentCategory.FACULTY_NAVIGATION,
    IntentCategory.PLANNING,
    IntentCategory.GREETING_HELP,
    IntentCategory.FORBIDDEN_WRITE,
    IntentCategory.OUT_OF_SCOPE,
)

NON_TOOL_INTENTS: frozenset[IntentCategory] = frozenset(
    {
        IntentCategory.GREETING_HELP,
        IntentCategory.FORBIDDEN_WRITE,
        IntentCategory.OUT_OF_SCOPE,
    }
)

ACTIONABLE_ERP_INTENTS: frozenset[IntentCategory] = frozenset(
    cat for cat in IntentCategory if cat not in NON_TOOL_INTENTS
)


class StudentClassificationOutput(BaseModel):
    """Structured-output schema for student LLM fallback classification."""

    intents: list[
        Literal[
            "lms",
            "academic",
            "examination",
            "personal_info",
            "planning",
            "greeting_help",
            "forbidden_write",
            "out_of_scope",
        ]
    ] = Field(
        default_factory=list,
        description=(
            "All student ERP intent categories matching the user query. "
            "Include multiple categories when the query spans multiple modules."
        ),
    )


class FacultyClassificationOutput(BaseModel):
    """Structured-output schema for faculty LLM fallback classification."""

    intents: list[
        Literal[
            "faculty_lms_read",
            "faculty_lms_write",
            "faculty_hr_payroll",
            "faculty_navigation",
            "planning",
            "greeting_help",
            "forbidden_write",
            "out_of_scope",
        ]
    ] = Field(
        default_factory=list,
        description=(
            "All faculty ERP intent categories matching the user query. "
            "Include multiple categories when the query spans multiple modules."
        ),
    )


class ClassificationResult(BaseModel):
    """Canonical result emitted by Stage 1 intent classification."""

    role: Literal["student", "teacher"]
    all_classified_intents: list[IntentCategory]
    source: Literal[
        "lexical_probes",
        "llm_fallback",
        "slot_followup",
        "default_out_of_scope",
    ] = "lexical_probes"
    normalized_query: str = ""
    elapsed_ms: float = 0.0

    @property
    def intent_values(self) -> list[str]:
        return [intent.value for intent in self.all_classified_intents]


class AuthContext(BaseModel):
    """Authenticated user context closed over by Stage 2 tool factories."""

    user_id: str
    role: Literal["student", "teacher"]
    email: str | None = None
    student_id: str | None = None
    semester: int | None = None
    teacher_id: str | None = None
    teacher: dict[str, Any] = Field(default_factory=dict)

    @property
    def cache_user_key(self) -> str:
        if self.role == "student" and self.student_id:
            return f"student:{self.student_id}"
        if self.role == "teacher" and self.teacher_id:
            return f"teacher:{self.teacher_id}"
        return f"user:{self.user_id}"

    @property
    def user_label(self) -> str:
        if self.email:
            return self.email
        if self.teacher and self.teacher.get("employee_id"):
            return str(self.teacher["employee_id"])
        return self.user_id


class SessionSlotState(BaseModel):
    """Compact 5-minute per-user conversational slot state."""

    last_intents: list[IntentCategory] = Field(default_factory=list)
    last_course_code: str | None = None
    last_course_name: str | None = None
    last_assignment_id: str | None = None
    updated_at: float = 0.0

    def to_prompt_summary(self) -> str | None:
        """Render non-empty slots as a tiny hint string for LLM context."""
        parts: list[str] = []
        if self.last_intents:
            parts.append(
                "recent_topics="
                + ",".join(intent.value for intent in self.last_intents)
            )
        if self.last_course_code:
            label = self.last_course_code
            if self.last_course_name:
                label = f"{label} ({self.last_course_name})"
            parts.append(f"recent_course={label}")
        if self.last_assignment_id:
            parts.append(f"recent_assignment_id={self.last_assignment_id}")
        if not parts:
            return None
        return "Session context (use only if the user's message refers back to it): " + "; ".join(parts)


class TurnSideEffects(BaseModel):
    """Side-effects captured during tool execution (e.g. guided upload card)."""

    action: dict[str, Any] | None = None
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    courses: list[dict[str, Any]] = Field(default_factory=list)
    kind_override: str | None = None
    title_override: str | None = None


class SSEMetaEvent(BaseModel):
    """Payload for `event: meta` in `/api/assistant/chat/stream`."""

    kind: str = "help"
    title: str = "ERP Assistant"
    intents: list[str] = Field(default_factory=list)
    streaming: bool = True


class SSETokenEvent(BaseModel):
    """Payload for `event: token` in `/api/assistant/chat/stream`."""

    text: str


class SSEActionEvent(BaseModel):
    """Payload for `event: action` (e.g. `upload_confirmation`)."""

    model_config = ConfigDict(extra="allow")

    type: str
    assignment: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """JSON payload returned by synchronous-contract `/api/assistant/chat`."""

    message: str
    kind: str = "help"
    title: str = "ERP Assistant"
    intents: list[str] = Field(default_factory=list)
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    courses: list[dict[str, Any]] = Field(default_factory=list)
    action: dict[str, Any] | None = None


def format_sse(event: str, data: Any) -> str:
    """Serialize an SSE frame (`event: <name>\\ndata: <json>\\n\\n`)."""
    if isinstance(data, BaseModel):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
