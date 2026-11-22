"""Assignment assistant host."""

from datetime import datetime
import re
from typing import Any

from erp_ai.tools.academic_tools import (
    format_attendance_summary,
    format_fees_summary,
    format_profile_summary,
    format_results_summary,
    get_attendance_summary,
    get_fees_summary,
    get_results_summary,
    get_student_profile,
)
from erp_ai.tools.examination_tools import (
    format_admit_card,
    format_backlog,
    format_exam_timetable,
    get_admit_card,
    get_backlog_courses,
    get_exam_timetable,
)
from erp_ai.tools.lms_tools.assignment_tools import (
    find_assignment,
    find_assignment_matches,
    list_current_assignments,
    list_my_courses,
)
import json
from collections.abc import Iterator

from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException
from fastapi.responses import StreamingResponse
from faculty_assistant import faculty_fallback, resolve_faculty_message
from gemini_agent import invoke_erp_agent, invoke_faculty_agent, stream_erp_agent, stream_faculty_agent
from pydantic import BaseModel

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    message: str


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _role(access_token: str | None) -> str:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")
    from main import verify_token

    return verify_token(access_token)["role"]


def _current_teacher(access_token: str | None) -> tuple[dict[str, Any], str]:
    from faculty_api import _require_teacher

    return _require_teacher(access_token)


def _faculty_reply(message: str, teacher: dict[str, Any], teacher_id: str) -> dict[str, Any]:
    ruled = resolve_faculty_message(message, teacher, teacher_id)
    if ruled is not None:
        return ruled
    try:
        answer = invoke_faculty_agent(
            question=message,
            teacher_id=teacher_id,
            teacher=teacher,
        )
    except Exception as exc:
        print(f"FACULTY_AGENT_ERROR: {type(exc).__name__}: {exc}")
        return faculty_fallback()
    return {
        "message": answer,
        "kind": "help",
        "title": "Faculty assistant",
        "assignments": [],
        "courses": [],
        "action": None,
    }


def _current_student(
    access_token: str | None,
) -> tuple[dict[str, Any], str, int]:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    from main import verify_token

    user = verify_token(access_token)

    if user["role"] != "student":
        raise HTTPException(
            status_code=403,
            detail="Only students can use the assignment assistant",
        )

    with get_connection() as conn:
        student = conn.execute(
            "SELECT id, semester FROM students WHERE user_id = %s",
            (user["id"],),
        ).fetchone()

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student profile not found",
        )

    return user, str(student[0]), student[1]


def _parse_due_date(value: str) -> datetime:
    """Parse a deadline and normalize it to the server's local timezone."""
    due_date = datetime.fromisoformat(value)
    return due_date.astimezone()


def _date_label(value: str) -> str:
    return _parse_due_date(value).strftime("%d %b %Y, %I:%M %p")


def _effective_status(
    assignment: dict[str, Any],
    now: datetime,
) -> str:
    """Calculate overdue status without overriding submission statuses."""
    status = (assignment.get("status") or "pending").casefold()

    if status in ("submitted", "late"):
        return status

    try:
        due_date = _parse_due_date(assignment["due_date"])
    except (ValueError, TypeError, KeyError):
        return status

    if status == "overdue" or (
        status == "pending" and due_date <= now
    ):
        return "overdue"

    return status


def _has_phrase(text: str, phrase: str) -> bool:
    """Match a phrase with word boundaries (avoids submit⊂submitted, due⊂overdue)."""
    pattern = r"\b" + re.escape(phrase) + r"\b"
    return re.search(pattern, text) is not None


def _has_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_has_phrase(text, phrase) for phrase in phrases)


def _mentions_assignment(text: str) -> bool:
    return re.search(r"\bassignments?\b", text) is not None


def _answer(
    message: str,
    *,
    kind: str,
    title: str,
    assignments: list[dict[str, Any]] | None = None,
    courses: list[dict[str, Any]] | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "message": message,
        "kind": kind,
        "title": title,
        "assignments": assignments or [],
        "courses": courses or [],
        "action": action,
    }


def _deadline_answer(
    assignments: list[dict[str, Any]],
    semester: int,
    normalized: str,
) -> dict[str, Any]:
    now = datetime.now().astimezone()

    if not assignments:
        return _answer(
            f"There are no assignments in your current semester "
            f"({semester}).",
            kind="empty",
            title="No assignments found",
        )

    upcoming = []

    for item in assignments:
        try:
            due_date = _parse_due_date(item["due_date"])
        except (ValueError, TypeError, KeyError):
            continue

        if (
            _effective_status(item, now) == "pending"
            and due_date > now
        ):
            upcoming.append((due_date, item))

    upcoming.sort(key=lambda pair: pair[0])

    if not upcoming:
        return _answer(
            "You have no pending assignments with upcoming deadlines.",
            kind="empty",
            title="No upcoming deadlines",
        )

    wants_latest = _has_any_phrase(
        normalized,
        ("latest", "last date"),
    )

    selected = upcoming[-1][1] if wants_latest else upcoming[0][1]
    direction = "latest" if wants_latest else "nearest upcoming"

    return _answer(
        f"Your {direction} assignment deadline is "
        f"{_date_label(selected['due_date'])}.",
        kind="deadline",
        title=f"{direction.title()} deadline",
        assignments=[selected],
    )


def _upload_answer(
    message: str,
    assignments: list[dict[str, Any]],
) -> dict[str, Any]:
    matches = find_assignment_matches(message, assignments)

    if len(matches) == 1:
        assignment = matches[0]
        return _answer(
            f"I found {assignment['title']} for "
            f"{assignment['course_code']} - {assignment['course_name']}. "
            "Attach your PDF or DOCX and confirm the upload below.",
            kind="upload",
            title="Ready for your file",
            action={
                "type": "upload_confirmation",
                "assignment": assignment,
            },
        )

    if len(matches) > 1:
        return _answer(
            f"I found {len(matches)} matching assignments. "
            "Reply with the full title, or include the assignment number "
            "(for example: upload assignment 1 for DBMS).",
            kind="assignment_list",
            title="Choose an assignment to upload",
            assignments=matches,
        )

    return _answer(
        "Mention one subject, course code, or assignment title so I can "
        "identify the correct upload destination.",
        kind="help",
        title="Which assignment should I use?",
    )


def _resolve_academic(
    message: str,
    student_id: str,
) -> dict[str, Any] | None:
    """Fast path for Academic read-only intents."""
    normalized = message.casefold()

    asks_profile = _has_any_phrase(
        normalized,
        (
            "my profile",
            "student profile",
            "student detail",
            "student details",
            "personal details",
            "who am i",
            "my rrn",
            "my enrollment",
        ),
    )

    # Keep this for direct attendance asks. Longer "plan around my attendance"
    # questions should go to the fast Gemini context path instead.
    creative_ask = _has_any_phrase(
        normalized,
        (
            "plan",
            "routine",
            "schedule",
            "invent",
            "based on",
            "recommend",
            "suggest",
            "advice",
            "focus on",
            "prioritize",
            "strategy",
        ),
    )
    asks_attendance = (
        not creative_ask
        and (
            _has_any_phrase(
                normalized,
                (
                    "my attendance",
                    "attendance summary",
                    "attendance details",
                    "attendance percentage",
                    "attendance %",
                    "attendance percent",
                    "how much attendance",
                    "what is my attendance",
                    "what's my attendance",
                    "whats my attendance",
                    "check attendance",
                    "current attendance",
                ),
            )
            or (
                _has_phrase(normalized, "attendance")
                and _has_any_phrase(
                    normalized,
                    ("show", "list", "view", "what", "tell", "how", "check", "my", "%"),
                )
            )
        )
    )

    asks_results = _has_any_phrase(
        normalized,
        (
            "my results",
            "result details",
            "my sgpa",
            "semester results",
            "exam results",
        ),
    ) or (
        _has_phrase(normalized, "result")
        and _has_any_phrase(normalized, ("show", "list", "view", "my", "tell"))
    )

    asks_fees = _has_any_phrase(
        normalized,
        (
            "my fees",
            "fee details",
            "fees details",
            "fee ledger",
            "outstanding fees",
            "fees outstanding",
        ),
    ) or (
        _has_any_phrase(normalized, ("fee", "fees"))
        and _has_any_phrase(normalized, ("show", "list", "view", "my", "tell"))
        and not _has_any_phrase(
            normalized,
            ("backlog", "exam fee", "exam fees"),
        )
    )

    try:
        if asks_profile:
            profile = get_student_profile(student_id)
            return _answer(
                format_profile_summary(profile),
                kind="academic_profile",
                title="Student profile",
            )

        if asks_attendance:
            summary = get_attendance_summary(student_id)
            return _answer(
                format_attendance_summary(summary),
                kind="academic_attendance",
                title="Attendance summary",
            )

        if asks_results:
            payload = get_results_summary(student_id)
            return _answer(
                format_results_summary(payload),
                kind="academic_results",
                title="Result details",
            )

        if asks_fees:
            payload = get_fees_summary(student_id)
            return _answer(
                format_fees_summary(payload),
                kind="academic_fees",
                title="Fees details",
            )
    except ValueError as exc:
        return _answer(
            str(exc),
            kind="empty",
            title="Academic data unavailable",
        )

    return None


def _resolve_examination(
    message: str,
    student_id: str,
) -> dict[str, Any] | None:
    """Fast path for Examination read-only intents."""
    normalized = message.casefold()

    asks_admit = _has_any_phrase(
        normalized,
        (
            "admit card",
            "hall ticket",
            "my admit",
            "show admit",
            "download admit",
        ),
    )

    asks_timetable = _has_any_phrase(
        normalized,
        (
            "exam timetable",
            "examination timetable",
            "exam schedule",
            "exam dates",
            "my exam timetable",
            "show timetable",
            "exam time table",
        ),
    ) or (
        _has_any_phrase(normalized, ("timetable", "time table"))
        and _has_any_phrase(normalized, ("exam", "examination", "my", "show", "list"))
    )

    asks_backlog_write = _has_phrase(normalized, "backlog") and _has_any_phrase(
        normalized,
        (
            "register",
            "registration",
            "enroll",
            "enrol",
            "pay",
            "payment",
            "submit backlog",
        ),
    )

    asks_backlog = _has_any_phrase(
        normalized,
        (
            "my backlog",
            "backlog courses",
            "backlog papers",
            "backlog subjects",
            "show backlog",
            "list backlog",
            "arrear",
            "arrears",
        ),
    ) or (
        _has_phrase(normalized, "backlog")
        and _has_any_phrase(
            normalized,
            ("show", "list", "view", "my", "tell", "what"),
        )
    )

    # Prefer examination intents before academic fees when backlog is mentioned.
    try:
        if asks_admit:
            payload = get_admit_card(student_id)
            return _answer(
                format_admit_card(payload),
                kind="exam_admit_card",
                title="Admit card",
            )

        if asks_timetable:
            payload = get_exam_timetable(student_id)
            return _answer(
                format_exam_timetable(payload),
                kind="exam_timetable",
                title="Exam timetable",
            )

        if asks_backlog_write:
            return _answer(
                "Backlog registration and payment are not available in chat "
                "(read-only). Use the Examination module or Pay Online UI. "
                "Ask 'my backlog' if you want the course list and fees.",
                kind="help",
                title="Backlog registration unavailable",
            )

        if asks_backlog:
            payload = get_backlog_courses(student_id)
            return _answer(
                format_backlog(payload),
                kind="exam_backlog",
                title="Backlog courses",
            )
    except ValueError as exc:
        return _answer(
            str(exc),
            kind="empty",
            title="Examination data unavailable",
        )

    return None


def _is_creative_ask(normalized: str) -> bool:
    """Planning/advice prompts get an instant grounded live-data plan."""
    return _has_any_phrase(
        normalized,
        (
            "plan",
            "routine",
            "schedule",
            "invent",
            "based on",
            "recommend",
            "suggest",
            "advice",
            "focus on",
            "focus this",
            "what should i",
            "what should i do",
            "prioritize",
            "priority",
            "strategy",
            "summarize my",
            "help me decide",
            "catch up",
            "workload",
        ),
    )


def _resolve_rule_based(
    message: str,
    student_id: str,
    semester: int,
) -> dict[str, Any] | None:
    """Return a structured answer for LMS/Academic/Exam intents, else None."""
    normalized_early = message.casefold()
    # Planning / advice prompts get an instant grounded plan from live ERP
    # data so response time stays near chip-speed instead of a long model wait.
    if _is_creative_ask(normalized_early):
        from erp_ai.tools.academic_tools.quick_plans import build_student_focus_plan

        return _answer(
            build_student_focus_plan(student_id, semester),
            kind="help",
            title="Focus plan",
        )

    examination = _resolve_examination(message, student_id)
    if examination is not None:
        return examination

    academic = _resolve_academic(message, student_id)
    if academic is not None:
        return academic

    assignments = list_current_assignments(student_id, semester)
    normalized = normalized_early

    asks_for_list = _has_any_phrase(
        normalized,
        ("show", "list", "view", "display", "give me"),
    ) and _mentions_assignment(normalized)

    asks_for_count = _has_any_phrase(
        normalized,
        ("how many", "count", "number of", "total"),
    ) and _mentions_assignment(normalized)

    asks_for_pending = _has_any_phrase(
        normalized,
        (
            "pending assignment",
            "pending assignments",
            "not submitted",
            "haven't submitted",
            "have not submitted",
            "yet to submit",
            "status is pending",
            "status pending",
            "whose status is pending",
            "whose staus is pending",  # common typo
        ),
    ) or (
        _has_phrase(normalized, "pending")
        and (
            _mentions_assignment(normalized)
            or _has_phrase(normalized, "status")
            or _has_phrase(normalized, "staus")
        )
    )

    asks_for_overdue = _has_any_phrase(
        normalized,
        (
            "overdue",
            "past deadline",
            "past due",
            "missed deadline",
            "expired",
            "expire",
            "status is expired",
            "status expired",
        ),
    )

    asks_for_submitted = _has_any_phrase(
        normalized,
        (
            "submitted assignment",
            "submitted assignments",
            "already submitted",
            "my submissions",
        ),
    ) or (
        _has_phrase(normalized, "submitted")
        and _mentions_assignment(normalized)
    )

    asks_for_status = (
        asks_for_pending or asks_for_overdue or asks_for_submitted
    )

    asks_for_dates = _has_any_phrase(
        normalized,
        (
            "last date",
            "latest",
            "next",
            "due date",
            "deadline",
            "due",
            "closes soon",
            "close soon",
            "closes first",
            "closing first",
            "nearest deadline",
            "upcoming deadline",
            "earliest deadline",
            "which assignment is due",
        ),
    ) and (
        _mentions_assignment(normalized)
        or _has_any_phrase(
            normalized,
            (
                "deadline",
                "due date",
                "last date",
                "closes soon",
                "close soon",
            ),
        )
    )

    # Word-boundary match so "submit" does not hit "submitted".
    asks_to_upload = _has_any_phrase(
        normalized,
        ("upload", "submit", "send"),
    ) and not asks_for_submitted

    # Status filters must run before upload/deadline (avoids false matches).
    now = datetime.now().astimezone()

    if asks_for_overdue:
        filtered_assignments = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
            if _effective_status(item, now) == "overdue"
        ]
        result_title = "Expired assignments"
        count = len(filtered_assignments)
        if count == 0:
            return _answer(
                f"No expired assignments in semester {semester}.",
                kind="empty",
                title=result_title,
            )
        return _answer(
            f"{count} expired assignment(s) in semester {semester}.",
            kind="assignment_list",
            title=result_title,
            assignments=filtered_assignments,
        )

    if asks_for_pending:
        filtered_assignments = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
            if _effective_status(item, now) == "pending"
        ]
        result_title = "Pending assignments"
        count = len(filtered_assignments)
        if count == 0:
            return _answer(
                f"No pending assignments in semester {semester}.",
                kind="empty",
                title=result_title,
            )
        return _answer(
            f"{count} pending assignment(s) in semester {semester}.",
            kind="assignment_list",
            title=result_title,
            assignments=filtered_assignments,
        )

    if asks_for_submitted:
        filtered_assignments = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
            if _effective_status(item, now) in ("submitted", "late")
        ]
        result_title = "Submitted assignments"
        count = len(filtered_assignments)
        if count == 0:
            return _answer(
                f"No submitted assignments in semester {semester}.",
                kind="empty",
                title=result_title,
            )
        return _answer(
            f"{count} submitted assignment(s) in semester {semester}.",
            kind="assignment_list",
            title=result_title,
            assignments=filtered_assignments,
        )

    # Upload requests require an identified assignment and user confirmation.
    if asks_to_upload:
        return _upload_answer(message, assignments)

    # Upcoming and latest deadline requests.
    if asks_for_dates:
        return _deadline_answer(assignments, semester, normalized)

    # Return details for a specifically named assignment.
    if (
        _mentions_assignment(normalized)
        and not asks_for_list
        and not asks_for_count
        and not asks_for_status
    ):
        assignment = find_assignment(message, assignments)

        if assignment:
            return _answer(
                f"Here are the details for {assignment['title']}.",
                kind="assignment_list",
                title="Assignment details",
                assignments=[assignment],
            )

        matches = find_assignment_matches(message, assignments)
        if len(matches) > 1:
            return _answer(
                f"I found {len(matches)} matching assignments. "
                "Ask about one by full title or assignment number.",
                kind="assignment_list",
                title="Matching assignments",
                assignments=matches,
            )

    # Return assignment lists and counts.
    if asks_for_count or asks_for_list or _has_phrase(normalized, "semester"):
        tagged = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
        ]
        count = len(tagged)

        if count == 0:
            return _answer(
                f"No assignments in semester {semester}.",
                kind="empty",
                title=f"Semester {semester} assignments",
            )

        if asks_for_count and not asks_for_list:
            return _answer(
                f"{count} assignment(s) in semester {semester}.",
                kind="assignment_list",
                title=f"Semester {semester} assignments",
                assignments=tagged,
            )

        return _answer(
            f"{count} assignment(s) in semester {semester}:",
            kind="assignment_list",
            title=f"Semester {semester} assignments",
            assignments=tagged,
        )

    # Course questions use structured database results.
    asks_for_courses = _has_any_phrase(
        normalized,
        (
            "course",
            "courses",
            "subject",
            "subjects",
            "teacher",
            "teachers",
            "faculty",
            "instructor",
            "enrolled",
        ),
    )

    if asks_for_courses:
        courses = list_my_courses(student_id, semester)

        if not courses:
            return _answer(
                f"No enrolled courses were found for semester {semester}.",
                kind="empty",
                title="No courses found",
            )

        return _answer(
            f"You are enrolled in {len(courses)} courses "
            f"in semester {semester}.",
            kind="course_list",
            title="Your enrolled courses",
            courses=courses,
        )

    return None


def _gemini_fallback_answer() -> dict[str, Any]:
    return _answer(
        "I can help with your courses, assignments, deadlines, "
        "submission status, guided uploads, academic profile/"
        "attendance/results/fees, and exam timetable/admit card/"
        "backlog (read-only). "
        "Try asking: 'list my assignments', 'my attendance', "
        "'exam timetable', 'admit card', or 'my backlog'. "
        "The live Gemini model is temporarily unavailable; "
        "LMS, Academic, and Examination questions still work without it.",
        kind="help",
        title="ERP Assistant",
    )


def _iter_text_chunks(text: str, size: int = 24) -> Iterator[str]:
    if not text:
        return
    for index in range(0, len(text), size):
        yield text[index:index + size]


@router.post("/chat")
def chat(
    data: ChatRequest,
    access_token: str | None = Cookie(default=None),
):
    if _role(access_token) == "teacher":
        teacher, teacher_id = _current_teacher(access_token)
        message = data.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")
        return _faculty_reply(message, teacher, teacher_id)

    _user, student_id, semester = _current_student(access_token)

    message = data.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty",
        )

    ruled = _resolve_rule_based(message, student_id, semester)
    if ruled is not None:
        return ruled

    # Delegate other questions to the LangGraph ERP agent.
    try:
        answer = invoke_erp_agent(
            question=message,
            student_id=student_id,
            semester=semester,
        )
    except Exception as exc:
        print(f"GEMINI_AGENT_ERROR: {type(exc).__name__}: {exc}")
        return _gemini_fallback_answer()

    return _answer(
        answer,
        kind="help",
        title="ERP Assistant",
    )


@router.post("/chat/stream")
def chat_stream(
    data: ChatRequest,
    access_token: str | None = Cookie(default=None),
):
    """SSE stream: typing waits on client until the first token event."""
    if _role(access_token) == "teacher":
        teacher, teacher_id = _current_teacher(access_token)
        message = data.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message cannot be empty")

        def faculty_stream() -> Iterator[str]:
            ruled = resolve_faculty_message(message, teacher, teacher_id)
            if ruled is not None:
                yield _sse(
                    "meta",
                    {"kind": ruled["kind"], "title": ruled["title"], "streaming": False},
                )
                for chunk in _iter_text_chunks(ruled.get("message") or ""):
                    yield _sse("token", {"text": chunk})
                yield _sse("done", {})
                return

            yield _sse(
                "meta",
                {"kind": "help", "title": "Faculty assistant", "streaming": True},
            )
            produced = False
            try:
                for token in stream_faculty_agent(
                    question=message,
                    teacher_id=teacher_id,
                    teacher=teacher,
                ):
                    if not token:
                        continue
                    produced = True
                    yield _sse("token", {"text": token})
            except Exception as exc:
                print(f"FACULTY_STREAM_ERROR: {type(exc).__name__}: {exc}")
            if not produced:
                for chunk in _iter_text_chunks(faculty_fallback()["message"]):
                    yield _sse("token", {"text": chunk})
            yield _sse("done", {})

        return StreamingResponse(
            faculty_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    _user, student_id, semester = _current_student(access_token)

    message = data.message.strip()
    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty",
        )

    def event_stream() -> Iterator[str]:
        ruled = _resolve_rule_based(message, student_id, semester)
        if ruled is not None:
            yield _sse(
                "meta",
                {
                    "kind": ruled["kind"],
                    "title": ruled["title"],
                    "streaming": False,
                },
            )
            for chunk in _iter_text_chunks(ruled.get("message") or ""):
                yield _sse("token", {"text": chunk})
            if ruled.get("assignments"):
                yield _sse("assignments", ruled["assignments"])
            if ruled.get("courses"):
                yield _sse("courses", ruled["courses"])
            if ruled.get("action"):
                yield _sse("action", ruled["action"])
            yield _sse("done", {})
            return

        yield _sse(
            "meta",
            {
                "kind": "help",
                "title": "ERP Assistant",
                "streaming": True,
            },
        )

        produced = False
        try:
            for token in stream_erp_agent(
                question=message,
                student_id=student_id,
                semester=semester,
            ):
                if not token:
                    continue
                produced = True
                yield _sse("token", {"text": token})
        except Exception as exc:
            print(f"GEMINI_STREAM_ERROR: {type(exc).__name__}: {exc}")
            if not produced:
                fallback = _gemini_fallback_answer()
                for chunk in _iter_text_chunks(fallback["message"]):
                    yield _sse("token", {"text": chunk})
            else:
                yield _sse(
                    "token",
                    {
                        "text": "\n\n(The live model interrupted. "
                        "You can retry this question.)",
                    },
                )

        if not produced:
            # Graph finished with only tool calls / empty text.
            fallback = _gemini_fallback_answer()
            for chunk in _iter_text_chunks(fallback["message"]):
                yield _sse("token", {"text": chunk})

        yield _sse("done", {})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
