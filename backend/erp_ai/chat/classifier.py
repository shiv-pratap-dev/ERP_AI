"""Stage 1 Parallel Multi-Category Intent Classifier (`classifier.py`).

Non-negotiable invariants:
1. ZERO database I/O — neither lexical probes nor the LLM fallback ever touch
   PostgreSQL or load ERP JSON payloads.
2. Strictly parallel execution — every category probe for the active role is an
   independent `async def` coroutine run concurrently via `asyncio.gather(*probes)`
   (never sequentially ordered to hide collisions).
3. Phrase collisions are resolved inside individual probes:
   - `_probe_planning` ignores `"schedule"` when paired with `"exam"` / `"timetable"`
     / `"extra class"` and ignores `"plan"` when paired with `"teaching plan"` /
     `"lecture plan"`.
   - `"backlog"` + write/payment verbs (`"pay"`, `"register"`) resolve cleanly to
     `forbidden_write` rather than read-only `examination` / `academic`.
4. Greeting stripping — when `greeting_help` co-occurs with any actionable ERP
   category (e.g. `"hey there, what is my attendance?"`), `greeting_help` is
   stripped during canonical normalization.
5. Zero-match paraphrase fallback — if `asyncio.gather` returns `[]` (and short
   conversational follow-up slot state doesn't deterministically disambiguate),
   invokes ONE lightweight structured-output LLM classifier call
   (`llm.with_structured_output(...)`, `temperature=0`, `max_output_tokens=120`)
   with ONLY the user query, optional compact `last_intents` slot state, and
   category descriptions. Defaults to `["out_of_scope"]` if empty or unavailable.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from chat_activity_log import get_turn_log
from erp_ai.chat.normalizer import (
    COURSE_ALIASES,
    extract_assignment_number,
    extract_course_code_hint,
    has_any_phrase,
    has_phrase,
    normalize_alnum,
    normalize_chat_text,
)
from erp_ai.chat.prompts import (
    FACULTY_CLASSIFIER_PROMPT as FACULTY_CLASSIFIER_SYSTEM_PROMPT,
    STUDENT_CLASSIFIER_PROMPT as STUDENT_CLASSIFIER_SYSTEM_PROMPT,
)
from erp_ai.chat.schemas import (
    ACTIONABLE_ERP_INTENTS,
    FACULTY_INTENT_CATEGORIES,
    STUDENT_INTENT_CATEGORIES,
    ClassificationResult,
    FacultyClassificationOutput,
    IntentCategory,
    SessionSlotState,
    StudentClassificationOutput,
)
from gemini_agent import get_llm, model_attempt_order

# Classifier instructions live in `erp_ai.chat.prompts` (zero ERP JSON / zero DB data).

# ============================================================================
# Shared Lexical Vocabularies & Collision Guards
# ============================================================================

_GREETING_ONLY_PHRASES: tuple[str, ...] = (
    "hi",
    "hey",
    "hello",
    "hola",
    "namaste",
    "yo",
    "sup",
    "hii",
    "hiii",
    "heya",
    "hey there",
    "hi there",
    "hello there",
    "good morning",
    "good afternoon",
    "good evening",
    "gm",
    "thanks",
    "thank you",
    "thx",
)

_CAPABILITY_HELP_PHRASES: tuple[str, ...] = (
    "what can you do",
    "how can you help",
    "what do you do",
    "help me",
    "capabilities",
    "what features",
    "what all can you do",
    "commands",
    "what can i ask",
    "who are you",
)

_CODE_SOLVING_OUT_OF_SCOPE_PHRASES: tuple[str, ...] = (
    "write a program",
    "write code",
    "write a code",
    "write python",
    "write java",
    "write c",
    "in c++",
    "in cpp",
    "in java",
    "in python",
    "in javascript",
    "binary search",
    "bubble sort",
    "merge sort",
    "quick sort",
    "dijkstra",
    "linked list implementation",
    "leetcode",
    "hackerrank",
    "reverse a string",
    "fibonacci",
    "factorial program",
    "palindrome program",
    "sql query for",
    "tell me a joke",
    "write an essay",
    "write a poem",
    "write a story",
    "capital of",
    "prime minister",
    "president of",
    "weather today",
    "bitcoin",
    "stock price",
    "cricket score",
    "ipl score",
    "recipe for",
    "ignore previous instructions",
    "ignore all rules",
)

_C_PLUS_PLUS_RE = re.compile(r"\b(?:c\+\+|cpp|golang|rust|javascript|typescript|html|css)\b")

_STUDENT_FORBIDDEN_WRITE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\b(?:pay|paying|make\s+payment|complete\s+payment|clear|settle)\b.*\b(?:fee|fees|dues|backlog|tuition|outstanding)\b",
        r"\b(?:fee|fees|backlog)\b.*\b(?:pay\s+now|pay\s+online\s+here|pay\s+in\s+chat)\b",
        r"\b(?:register|registration|apply|enrol|enroll)\b.*\b(?:backlog|backlogs|arrear|arrears|reappear)\b",
        r"\b(?:backlog|backlogs|arrear|arrears)\b.*\b(?:register|registration|apply|submit\s+form)\b",
        r"\b(?:change|reset|update|edit|modify)\b.*\b(?:password|profile|mobile\s+number|phone\s+number|email|dob|date\s+of\s+birth|address|name|rrn|attendance|marks|sgpa|grade)\b",
    )
)

_FACULTY_FORBIDDEN_WRITE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\b(?:change|reset|update|edit|modify)\b.*\b(?:password|salary|payslip|pay\s+slip|leave\s+balance|bank\s+account|pan|uan)\b",
        r"\b(?:apply|sanction|approve)\b.*\b(?:leave|od|comp\s*off)\b.*\b(?:for\s+me|right\s+now|in\s+chat|here)\b",
        r"\b(?:other\s+teacher|another\s+teacher|colleague'?s)\b.*\b(?:salary|payslip|leave)\b",
    )
)

# Check if query contains a course alias or code (useful for short follow-ups or upload/attendance hints)
def _mentions_known_course(normalized: str) -> bool:
    if extract_course_code_hint(normalized) is not None:
        return True
    alnum = normalize_alnum(normalized)
    for alias in COURSE_ALIASES:
        if has_phrase(alnum, alias):
            return True
    return False


def _has_strong_erp_noun(normalized: str, role: Literal["student", "teacher"]) -> bool:
    student_nouns = (
        "assignment",
        "assignments",
        "homework",
        "deadline",
        "deadlines",
        "due date",
        "attendance",
        "result",
        "results",
        "sgpa",
        "cgpa",
        "fee",
        "fees",
        "timetable",
        "time table",
        "admit card",
        "hall ticket",
        "backlog",
        "backlogs",
        "arrear",
        "arrears",
        "rrn",
        "enrollment",
        "semester",
        "mentor",
        "profile",
    )
    faculty_nouns = (
        "assignment",
        "assignments",
        "submission",
        "submissions",
        "unchecked",
        "ungraded",
        "pending check",
        "pending checks",
        "leave",
        "leaves",
        "payslip",
        "salary",
        "holiday",
        "holidays",
        "obe",
        "mark entry",
        "teaching plan",
        "extra class",
        "attendance register",
    )
    vocab = faculty_nouns if role == "teacher" else student_nouns
    return has_any_phrase(normalized, vocab)


def _strip_exam_and_teaching_plan_collisions(normalized: str) -> str:
    """Mask phrases whose 'schedule'/'plan' tokens belong to Examination or Faculty Navigation."""
    masked = normalized
    collision_phrases = (
        "exam schedule",
        "examination schedule",
        "exam schedules",
        "test schedule",
        "schedule of exam",
        "schedule of exams",
        "schedule for exam",
        "schedule for exams",
        "exam time table",
        "exam timetable",
        "examination timetable",
        "teaching plan",
        "teaching plans",
        "lecture plan",
        "lecture plans",
        "lesson plan",
        "extra class schedule",
        "extra class time table",
        "extra class timetable",
        "class timetable",
        "fee plan",
        "installment plan",
    )
    for phrase in collision_phrases:
        masked = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", masked)
    return re.sub(r"\s+", " ", masked).strip()


def _is_backlog_write_request(normalized: str) -> bool:
    return has_any_phrase(normalized, ("backlog", "backlogs", "arrear", "arrears")) and has_any_phrase(
        normalized,
        ("pay", "paying", "payment", "register", "registration", "apply", "enroll", "enrol"),
    )


def _is_fee_write_request(normalized: str) -> bool:
    return has_any_phrase(normalized, ("fee", "fees", "dues", "tuition")) and has_any_phrase(
        normalized,
        ("pay", "paying", "make payment", "complete payment", "pay online now"),
    ) and not has_any_phrase(
        normalized,
        ("paid", "how much", "show", "view", "list", "check", "summary", "ledger", "receipt", "details", "status"),
    )


# ============================================================================
# Student Zero-I/O Async Category Probes
# ============================================================================


async def _probe_student_lms(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None
    # Guard against coding homework asks like "write binary search in python"
    if _is_explicit_coding_or_general_ask(normalized) and not has_any_phrase(
        normalized,
        ("assignment", "assignments", "upload", "deadline", "due date", "pending", "overdue", "submitted", "my courses", "enrolled"),
    ):
        return None

    direct_lms_phrases = (
        "assignment",
        "assignments",
        "homework",
        "hw",
        "coursework",
        "due date",
        "due dates",
        "deadline",
        "deadlines",
        "pending work",
        "overdue",
        "past due",
        "missed deadline",
        "submitted",
        "my submissions",
        "not submitted",
        "yet to submit",
        "upload",
        "enrolled",
        "my courses",
        "my subjects",
        "enrolled courses",
        "course list",
        "subject list",
        "who teaches",
        "course instructor",
        "subject teacher",
    )
    if has_any_phrase(normalized, direct_lms_phrases):
        return IntentCategory.LMS

    if extract_assignment_number(normalized) is not None:
        return IntentCategory.LMS

    # "submit" (when not "submit backlog") or "due"
    if has_any_phrase(normalized, ("submit", "due", "closes soon", "closing soon")) and not has_any_phrase(
        normalized, ("backlog", "arrear", "fee", "fees", "leave")
    ):
        return IntentCategory.LMS

    # Asking about courses/teachers without collisions with backlog courses or failed courses
    if has_any_phrase(normalized, ("course", "courses", "subject", "subjects", "faculty", "teacher", "teachers", "instructor")) and not has_any_phrase(
        normalized,
        (
            "backlog",
            "arrear",
            "failed",
            "attendance",
            "exam timetable",
            "exam schedule",
            "admit card",
            "hall ticket",
            "result",
            "results",
        ),
    ):
        return IntentCategory.LMS

    return None


async def _probe_student_academic(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    attendance_phrases = (
        "attendance",
        "present",
        "absent",
        "classes missed",
        "missed classes",
        "missed lectures",
        "how many classes",
        "bunk",
        "bunked",
        "shortage",
        "75%",
        "75 percent",
        "classes attended",
        "attended classes",
    )
    if has_any_phrase(normalized, attendance_phrases):
        return IntentCategory.ACADEMIC
    if re.search(r"\b(?:miss|missed|skip|skipped|attended)\b.*\b(?:class|classes|lecture|lectures)\b", normalized):
        return IntentCategory.ACADEMIC

    results_phrases = (
        "result",
        "results",
        "sgpa",
        "cgpa",
        "gpa",
        "internal marks",
        "internals",
        "semester marks",
        "marksheet",
        "grade card",
        "earned credits",
        "failed courses",
    )
    if has_any_phrase(normalized, results_phrases):
        return IntentCategory.ACADEMIC
    if has_any_phrase(normalized, ("marks", "grades", "grade")) and not has_any_phrase(
        normalized, ("max marks", "backlog")
    ):
        return IntentCategory.ACADEMIC

    # Academic fees (read-only) — exclude backlog exam fees and pure fee-payment write commands
    if has_any_phrase(
        normalized,
        (
            "my fees",
            "fee details",
            "fees details",
            "fee ledger",
            "fee summary",
            "outstanding fees",
            "fees outstanding",
            "fee receipt",
            "tuition fee",
            "tuition fees",
            "paid fees",
            "pending fees",
            "fee due",
            "fees due",
        ),
    ) and not has_any_phrase(normalized, ("backlog", "arrear")):
        if not _is_fee_write_request(normalized):
            return IntentCategory.ACADEMIC

    if (
        has_any_phrase(normalized, ("fee", "fees", "outstanding"))
        and not has_any_phrase(normalized, ("backlog", "arrear", "exam fee", "exam fees"))
        and not _is_fee_write_request(normalized)
    ):
        return IntentCategory.ACADEMIC

    return None


async def _probe_student_examination(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    exam_phrases = (
        "exam timetable",
        "examination timetable",
        "exam time table",
        "examination time table",
        "exam schedule",
        "examination schedule",
        "exam date",
        "exam dates",
        "date sheet",
        "datesheet",
        "end term",
        "internal exam",
        "mid term",
        "admit card",
        "hall ticket",
        "exam center",
        "exam centre",
    )
    if has_any_phrase(normalized, exam_phrases):
        return IntentCategory.EXAMINATION

    if has_any_phrase(normalized, ("timetable", "time table")):
        return IntentCategory.EXAMINATION

    if has_any_phrase(normalized, ("when are my exams", "when is my exam", "upcoming exams", "exam papers")):
        return IntentCategory.EXAMINATION

    # Read-only backlog lookup (exclude forbidden backlog registration/payment asks unless explicitly asking to view/list too)
    if has_any_phrase(normalized, ("backlog", "backlogs", "arrear", "arrears", "back paper", "back papers")):
        if not _is_backlog_write_request(normalized) or has_any_phrase(
            normalized, ("show", "list", "view", "what are")
        ):
            return IntentCategory.EXAMINATION

    return None


async def _probe_student_personal_info(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    profile_phrases = (
        "my profile",
        "student profile",
        "personal info",
        "personal details",
        "student details",
        "student detail",
        "who am i",
        "my name",
        "my rrn",
        "rrn number",
        "what is my rrn",
        "enrollment number",
        "enrollment no",
        "my enrollment",
        "roll number",
        "roll no",
        "my branch",
        "my degree",
        "my school",
        "my dob",
        "date of birth",
        "my mentor",
        "faculty mentor",
        "who is my mentor",
        "admission batch",
        "my email",
        "current session",
        "academic session",
        "which semester",
        "what semester",
        "current semester",
        "my semester",
        "my current semester",
        "which sem",
        "what sem",
        "current sem",
        "which semester am i",
        "what semester am i",
    )
    if has_any_phrase(normalized, profile_phrases):
        # Guard against "edit/update my profile" which is forbidden_write
        if re.search(r"\b(?:change|reset|update|edit|modify)\b.*\b(?:profile|password|mobile|email|dob|address|name)\b", normalized):
            return None
        return IntentCategory.PERSONAL_INFO

    if (
        has_any_phrase(normalized, ("semester", "sem", "rrn", "mentor"))
        and has_any_phrase(normalized, ("am i", "i am", "i'm", "im in", "my", "who is"))
        and not has_any_phrase(
            normalized,
            (
                "assignment",
                "assignments",
                "result",
                "results",
                "sgpa",
                "fee",
                "fees",
                "course",
                "courses",
                "backlog",
                "timetable",
                "attendance",
            ),
        )
    ):
        return IntentCategory.PERSONAL_INFO

    return None


async def _probe_planning(normalized: str) -> IntentCategory | None:
    """Shared planning probe with built-in collision immunity (`exam schedule`, `teaching plan`)."""
    if not normalized:
        return None

    masked = _strip_exam_and_teaching_plan_collisions(normalized)
    if not masked:
        return None

    unambiguous_planning_phrases = (
        "study plan",
        "weekly plan",
        "daily plan",
        "action plan",
        "study routine",
        "weekly routine",
        "daily routine",
        "study schedule",
        "prioritize",
        "priority",
        "priorities",
        "catch up",
        "focus area",
        "focus areas",
        "focus on",
        "focus this week",
        "what should i focus",
        "what should i do first",
        "how should i plan",
        "plan my week",
        "plan my study",
        "grading plan",
        "workload plan",
        "organize my week",
        "time management",
    )
    if has_any_phrase(masked, unambiguous_planning_phrases):
        return IntentCategory.PLANNING

    # Bare "plan" / "routine" / "strategy" / "schedule" only when not part of exam/navigation asks
    if has_any_phrase(masked, ("plan", "routine", "strategy")):
        if not has_any_phrase(normalized, ("teaching plan", "lecture plan", "lesson plan")):
            return IntentCategory.PLANNING

    if has_phrase(masked, "schedule") and has_any_phrase(
        masked, ("study", "my week", "weekly", "daily", "workload", "grading", "revision")
    ):
        return IntentCategory.PLANNING

    return None


async def _probe_greeting_help(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    cleaned = re.sub(r"[!?.,:;]+", " ", normalized).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if cleaned in _GREETING_ONLY_PHRASES:
        return IntentCategory.GREETING_HELP

    if has_any_phrase(cleaned, _CAPABILITY_HELP_PHRASES):
        return IntentCategory.GREETING_HELP

    if cleaned in {"help", "menu", "options", "start"}:
        return IntentCategory.GREETING_HELP

    # Leading greeting token (if paired with real ERP intents, canonical normalizer drops greeting_help)
    tokens = cleaned.split()
    if tokens and tokens[0] in {"hi", "hey", "hello", "hola", "namaste", "hii"} and len(tokens) <= 4:
        return IntentCategory.GREETING_HELP

    return None


async def _probe_student_forbidden_write(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    if _is_backlog_write_request(normalized) or _is_fee_write_request(normalized):
        return IntentCategory.FORBIDDEN_WRITE

    for pattern in _STUDENT_FORBIDDEN_WRITE_PATTERNS:
        if pattern.search(normalized):
            return IntentCategory.FORBIDDEN_WRITE

    return None


def _is_explicit_coding_or_general_ask(normalized: str) -> bool:
    if has_any_phrase(normalized, _CODE_SOLVING_OUT_OF_SCOPE_PHRASES):
        return True
    if _C_PLUS_PLUS_RE.search(normalized) and has_any_phrase(
        normalized, ("write", "code", "program", "implement", "algorithm", "function", "solve", "explain")
    ):
        return True
    if re.search(
        r"\b(?:write|implement|code|solve|debug)\b.*\b(?:algorithm|program|function|class|array|tree|graph|sorting|search)\b",
        normalized,
    ):
        return True
    return False


async def _probe_out_of_scope(
    normalized: str,
    role: Literal["student", "teacher"],
) -> IntentCategory | None:
    if not normalized:
        return None
    if _is_explicit_coding_or_general_ask(normalized) and not _has_strong_erp_noun(normalized, role):
        return IntentCategory.OUT_OF_SCOPE
    return None


# ============================================================================
# Faculty Zero-I/O Async Category Probes
# ============================================================================


def _is_faculty_create_assignment_ask(normalized: str) -> bool:
    create_verbs = (
        "create assignment",
        "create an assignment",
        "create a new assignment",
        "new assignment",
        "add assignment",
        "add an assignment",
        "post assignment",
        "post an assignment",
        "publish assignment",
        "publish an assignment",
        "give assignment",
        "make an assignment",
    )
    if has_any_phrase(normalized, create_verbs):
        # Exclude navigation questions like "where do I create an assignment" / "how to create assignment screen"
        if has_any_phrase(normalized, ("where is", "where do i", "which menu", "which screen", "how to open")):
            return False
        return True
    if re.search(
        r"\b(?:create|add|post|publish)\b.*\bassignment\b",
        normalized,
    ) and not has_any_phrase(normalized, ("where is", "where do i", "which menu", "which screen")):
        return True
    return False


def _is_faculty_navigation_ask(normalized: str) -> bool:
    nav_screen_topics = (
        "obe",
        "outcome based",
        "question wise",
        "question-wise",
        "question paper",
        "mark entry",
        "marks entry",
        "attendance register",
        "teaching plan",
        "lecture plan",
        "extra class",
        "makeup class",
        "service book",
    )
    if has_any_phrase(normalized, nav_screen_topics):
        return True

    nav_prefixes = (
        "where is",
        "where do i",
        "where can i",
        "how do i open",
        "how to open",
        "how do i find",
        "how to navigate",
        "which menu",
        "which tab",
        "which screen",
        "screen guide",
        "navigation",
        "how to mark attendance",
        "where to mark attendance",
        "where to enter marks",
    )
    if has_any_phrase(normalized, nav_prefixes):
        return True

    return False


async def _probe_faculty_lms_write(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None
    if _is_faculty_create_assignment_ask(normalized):
        return IntentCategory.FACULTY_LMS_WRITE
    return None


async def _probe_faculty_lms_read(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    # Pure create command shouldn't unnecessarily pull read-only unchecked tools unless also asking about courses/checks
    is_write = _is_faculty_create_assignment_ask(normalized)

    read_phrases = (
        "my courses",
        "courses i teach",
        "subjects i teach",
        "taught courses",
        "assigned courses",
        "student count",
        "student counts",
        "how many students",
        "enrolled students",
        "pending check",
        "pending checks",
        "unchecked",
        "ungraded",
        "submissions to check",
        "waiting to be checked",
        "yet to grade",
        "to grade",
        "student submissions",
        "replies",
        "my assignments",
        "published assignments",
        "active assignments",
    )
    if has_any_phrase(normalized, read_phrases):
        return IntentCategory.FACULTY_LMS_READ

    if not is_write and has_any_phrase(
        normalized,
        ("courses", "course", "assignments", "assignment", "submissions", "submission", "grading"),
    ) and not _is_faculty_navigation_ask(normalized):
        return IntentCategory.FACULTY_LMS_READ

    return None


async def _probe_faculty_hr_payroll(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None

    # Pure navigation question like "where is the payslip screen" -> handled by faculty_navigation
    pure_nav_only = has_any_phrase(
        normalized, ("where is", "where do i find", "which menu", "how to open")
    ) and not has_any_phrase(
        normalized, ("my leave", "leave balance", "my payslip", "my salary", "net pay", "gross pay", "upcoming holiday", "holidays")
    )
    if pure_nav_only:
        return None

    hr_payroll_phrases = (
        "leave",
        "leaves",
        "leave balance",
        "leave balances",
        "leave card",
        "casual leave",
        "earned leave",
        "medical leave",
        "vacation leave",
        "remaining leaves",
        "payslip",
        "pay slip",
        "salary",
        "net pay",
        "gross pay",
        "take home",
        "deductions",
        "earnings",
        "payroll",
        "holiday",
        "holidays",
        "upcoming holiday",
        "upcoming holidays",
        "next holiday",
        "academic holiday",
    )
    if has_any_phrase(normalized, hr_payroll_phrases):
        return IntentCategory.FACULTY_HR_PAYROLL

    return None


async def _probe_faculty_navigation(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None
    if _is_faculty_navigation_ask(normalized):
        return IntentCategory.FACULTY_NAVIGATION
    return None


async def _probe_faculty_forbidden_write(normalized: str) -> IntentCategory | None:
    if not normalized:
        return None
    for pattern in _FACULTY_FORBIDDEN_WRITE_PATTERNS:
        if pattern.search(normalized):
            return IntentCategory.FORBIDDEN_WRITE
    return None


# ============================================================================
# Canonical Normalization & Deduplication
# ============================================================================


def _canonicalize_intents(
    raw_intents: list[IntentCategory],
    role: Literal["student", "teacher"],
) -> list[IntentCategory]:
    """Deduplicate in canonical role order and strip `greeting_help` / `out_of_scope` when actionable intents exist."""
    allowed_order = (
        FACULTY_INTENT_CATEGORIES if role == "teacher" else STUDENT_INTENT_CATEGORIES
    )
    present = set(raw_intents)

    has_actionable = bool(present & ACTIONABLE_ERP_INTENTS)
    has_forbidden = IntentCategory.FORBIDDEN_WRITE in present

    if has_actionable or has_forbidden:
        # Spec: If `greeting_help` co-occurs with real ERP modules (e.g. "hey there, what is my attendance?"),
        # drop `greeting_help` so it goes straight to tool resolution.
        present.discard(IntentCategory.GREETING_HELP)
        present.discard(IntentCategory.OUT_OF_SCOPE)

    ordered = [cat for cat in allowed_order if cat in present]
    return ordered


def _resolve_short_slot_followup(
    normalized: str,
    role: Literal["student", "teacher"],
    session_slots: SessionSlotState | None,
) -> list[IntentCategory]:
    """Zero-I/O follow-up continuity when a user replies with just a course hint (e.g. 'what about DBMS?')."""
    if session_slots is None or not session_slots.last_intents:
        return []

    tokens = normalized.split()
    if not tokens or len(tokens) > 6:
        return []

    is_short_followup_phrasing = (
        _mentions_known_course(normalized)
        or extract_assignment_number(normalized) is not None
        or has_any_phrase(
            normalized,
            ("what about", "how about", "and for", "for that", "same for", "and pending", "and overdue"),
        )
    )
    if not is_short_followup_phrasing:
        return []

    actionable_prev = [
        intent
        for intent in session_slots.last_intents
        if intent in ACTIONABLE_ERP_INTENTS
    ]
    return _canonicalize_intents(actionable_prev, role)


# ============================================================================
# Zero-ERP-Data Structured-Output LLM Classifier Fallback
# ============================================================================

def _parse_json_intents(raw_text: Any) -> list[str]:
    """Extract an `intents` list from a plain-text or fenced JSON LLM reply."""
    import json
    import re

    text = str(raw_text or "").strip()
    if not text:
        return []
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and isinstance(data.get("intents"), list):
        return [str(v) for v in data["intents"]]
    return []


async def _llm_classify_fallback(
    query: str,
    role: Literal["student", "teacher"],
    session_slots: SessionSlotState | None = None,
) -> list[IntentCategory]:
    """Run ONE lightweight structured-output LLM classifier call (ZERO ERP data, ZERO DB queries)."""
    log = get_turn_log()
    schema_cls = (
        FacultyClassificationOutput
        if role == "teacher"
        else StudentClassificationOutput
    )
    system_prompt = (
        FACULTY_CLASSIFIER_SYSTEM_PROMPT
        if role == "teacher"
        else STUDENT_CLASSIFIER_SYSTEM_PROMPT
    )

    user_lines: list[str] = []
    if session_slots and session_slots.last_intents:
        recent = ", ".join(i.value for i in session_slots.last_intents)
        user_lines.append(f"Recent conversation intents (only if follow-up): {recent}")
    if session_slots and session_slots.last_course_code:
        user_lines.append(f"Recent course (only if follow-up): {session_slots.last_course_code}")
    user_lines.append(f"User query: {query.strip()}")
    human_text = "\n".join(user_lines)

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_text),
    ]

    for attempt_idx, model_name in enumerate(model_attempt_order(), start=1):
        try:
            base_llm = get_llm(
                model_name,
                temperature=0.0,
                max_output_tokens=2048,
                timeout=60,
                max_retries=1,
                include_thoughts=False,
                thinking_budget=0,
            )
            if log:
                log.thinking(
                    f"Lexical probes matched 0 categories — invoking structured LLM classifier "
                    f"(model={model_name}, attempt={attempt_idx})."
                )
            raw_values: list[str] = []
            try:
                try:
                    structured_llm = base_llm.with_structured_output(
                        schema_cls,
                        method="json_schema",
                    )
                except TypeError:
                    structured_llm = base_llm.with_structured_output(schema_cls)
                parsed = await structured_llm.ainvoke(messages)
                if isinstance(parsed, (StudentClassificationOutput, FacultyClassificationOutput)):
                    raw_values = list(parsed.intents or [])
                elif isinstance(parsed, dict):
                    raw_values = [str(v) for v in (parsed.get("intents") or [])]
            except Exception:
                # Fallback for local/OpenAI-compatible models that don't populate `parsed`
                json_messages = [
                    SystemMessage(
                        content=system_prompt
                        + '\n\nRespond ONLY with valid JSON: {"intents": ["..."]}.'
                    ),
                    HumanMessage(content=human_text),
                ]
                raw_reply = await base_llm.ainvoke(json_messages)
                raw_values = _parse_json_intents(getattr(raw_reply, "content", ""))

            converted: list[IntentCategory] = []
            for val in raw_values:
                try:
                    converted.append(IntentCategory(val))
                except ValueError:
                    continue

            canonical = _canonicalize_intents(converted, role)
            if canonical:
                return canonical
            return [IntentCategory.OUT_OF_SCOPE]
        except Exception as exc:
            if log:
                log.model_error(model_name, exc, attempt=attempt_idx)
            continue

    return []


# ============================================================================
# Public Entry Point: Parallel Stage 1 Classifier
# ============================================================================


async def classify_intents(
    query: str,
    role: Literal["student", "teacher"],
    *,
    session_slots: SessionSlotState | None = None,
    allow_llm_fallback: bool = True,
) -> ClassificationResult:
    """Execute Stage 1 Parallel Multi-Label Intent Classification (ZERO DB I/O).

    1. Runs all category probes for `role` concurrently via `asyncio.gather(*probes)`.
    2. Canonicalizes, deduplicates, and strips `greeting_help` when paired with real ERP modules.
    3. If lexical probes return `[]`, checks compact conversational follow-up slots,
       then invokes ONE zero-ERP-data structured-output LLM classifier call.
    """
    t0 = time.perf_counter()
    log = get_turn_log()
    normalized = normalize_chat_text(query)

    if not normalized:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result = ClassificationResult(
            role=role,
            all_classified_intents=[IntentCategory.GREETING_HELP],
            source="lexical_probes",
            normalized_query="",
            elapsed_ms=elapsed_ms,
        )
        if log:
            log.classified_intents(
                result.all_classified_intents,
                source=result.source,
                elapsed_ms=elapsed_ms,
            )
        return result

    if role == "teacher":
        probes = (
            _probe_faculty_lms_read(normalized),
            _probe_faculty_lms_write(normalized),
            _probe_faculty_hr_payroll(normalized),
            _probe_faculty_navigation(normalized),
            _probe_planning(normalized),
            _probe_greeting_help(normalized),
            _probe_faculty_forbidden_write(normalized),
            _probe_out_of_scope(normalized, "teacher"),
        )
    else:
        probes = (
            _probe_student_lms(normalized),
            _probe_student_academic(normalized),
            _probe_student_examination(normalized),
            _probe_student_personal_info(normalized),
            _probe_planning(normalized),
            _probe_greeting_help(normalized),
            _probe_student_forbidden_write(normalized),
            _probe_out_of_scope(normalized, "student"),
        )

    probe_outputs = await asyncio.gather(*probes)
    matched = [item for item in probe_outputs if item is not None]
    canonical = _canonicalize_intents(matched, role)

    if canonical:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result = ClassificationResult(
            role=role,
            all_classified_intents=canonical,
            source="lexical_probes",
            normalized_query=normalized,
            elapsed_ms=elapsed_ms,
        )
        if log:
            log.classified_intents(
                result.all_classified_intents,
                source=result.source,
                elapsed_ms=elapsed_ms,
            )
        return result

    # Check short conversational slot follow-up (e.g., "what about DBMS?" right after attendance)
    slot_followup = _resolve_short_slot_followup(normalized, role, session_slots)
    if slot_followup:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        result = ClassificationResult(
            role=role,
            all_classified_intents=slot_followup,
            source="slot_followup",
            normalized_query=normalized,
            elapsed_ms=elapsed_ms,
        )
        if log:
            log.classified_intents(
                result.all_classified_intents,
                source=result.source,
                elapsed_ms=elapsed_ms,
            )
        return result

    # 0 lexical matches -> invoke zero-ERP-context structured LLM classifier fallback
    if allow_llm_fallback:
        llm_intents = await _llm_classify_fallback(
            query,
            role,
            session_slots=session_slots,
        )
        if llm_intents:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            result = ClassificationResult(
                role=role,
                all_classified_intents=llm_intents,
                source="llm_fallback",
                normalized_query=normalized,
                elapsed_ms=elapsed_ms,
            )
            if log:
                log.classified_intents(
                    result.all_classified_intents,
                    source=result.source,
                    elapsed_ms=elapsed_ms,
                )
            return result

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    result = ClassificationResult(
        role=role,
        all_classified_intents=[IntentCategory.OUT_OF_SCOPE],
        source="default_out_of_scope",
        normalized_query=normalized,
        elapsed_ms=elapsed_ms,
    )
    if log:
        log.classified_intents(
            result.all_classified_intents,
            source=result.source,
            elapsed_ms=elapsed_ms,
        )
    return result
