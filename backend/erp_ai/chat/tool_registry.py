"""Stage 2 Deterministic Tool Resolver (`get_tools_for_intents`).

Maps canonical `IntentCategory` lists to scoped LangChain `StructuredTool`s
bound to the authenticated user's `AuthContext`.

Non-negotiable security & hygiene contracts:
- `student_id`, `teacher_id`, and `user_id` are closed over from `auth_ctx` and
  are NEVER exposed as LLM-writable tool arguments.
- Every tool returns pure JSON-serializable `dict` payloads (zero markdown).
- Read-only tools use the 30-second per-user `ToolResultCache`.
- `list_assignments` computes `effective_status` (`pending` -> `overdue` when
  past due), formats human-readable IST timestamps alongside ISO dates, and
  caps arrays at 25 items.
- `prepare_assignment_upload` records `turn_side_effects.action` with the exact
  `upload_confirmation` payload expected by `frontend/shared/erp-assistant.js`
  when and only when a single enrolled theory assignment matches unambiguously.
"""

from __future__ import annotations

from datetime import datetime
import time
from typing import Any, Callable, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from chat_activity_log import current_parallel_batch, get_turn_log
from erp_ai.chat.cache import (
    get_cached_tool_result,
    set_cached_tool_result,
    update_session_slots,
)
from erp_ai.chat.normalizer import (
    IST,
    compute_effective_assignment_status,
    expand_course_aliases,
    format_ist_datetime,
    has_phrase,
    match_course_from_list,
    normalize_alnum,
    normalize_chat_text,
)
from erp_ai.chat.schemas import (
    AuthContext,
    IntentCategory,
    TurnSideEffects,
)
from erp_ai.tools.academic_tools import (
    filter_attendance_subjects,
    get_attendance_summary,
    get_fees_summary,
    get_results_summary,
    get_student_profile,
)
from erp_ai.tools.examination_tools import (
    get_admit_card as load_admit_card_data,
    get_backlog_courses as load_backlog_data,
    get_exam_meta,
    get_exam_timetable as load_exam_timetable_data,
)
from erp_ai.tools.lms_tools.assignment_tools import (
    find_assignment_matches,
    list_current_assignments,
    list_my_courses as load_student_courses,
)
from faculty_assistant import (
    create_faculty_assignment,
    get_faculty_navigation_guide,
    teacher_assignments,
    teacher_courses,
    teacher_leave,
    teacher_payslip,
    upcoming_holidays,
)

MAX_TOOL_ITEMS = 25


# ============================================================================
# Pydantic LLM-visible input schemas (zero user/student/teacher IDs exposed!)
# ============================================================================


class EmptyToolInput(BaseModel):
    """Schema for tools requiring no LLM-supplied filter parameters."""


class ListAssignmentsInput(BaseModel):
    status_filter: Literal["pending", "overdue", "submitted"] | None = Field(
        default=None,
        description=(
            "Optional status filter: 'pending' (not submitted & future deadline), "
            "'overdue' (not submitted & past deadline), or 'submitted' (submitted/late)."
        ),
    )
    course_hint: str | None = Field(
        default=None,
        description="Optional course code, course name, or alias (e.g. 'DBMS', 'DSA', 'BCO002B').",
    )
    assignment_number: int | None = Field(
        default=None,
        ge=1,
        le=99,
        description="Optional assignment number (e.g. 1 for Assignment 1).",
    )


class PrepareUploadInput(BaseModel):
    course_hint: str | None = Field(
        default=None,
        description="Course code, subject name, or alias for the assignment to upload (e.g. 'DBMS', 'OS').",
    )
    assignment_number: int | None = Field(
        default=None,
        ge=1,
        le=99,
        description="Optional assignment number when a course has multiple assignments (e.g. 1).",
    )


class AttendanceInput(BaseModel):
    subject_hint: str | None = Field(
        default=None,
        description="Optional course code, subject name, or alias (e.g. 'DSA', 'OS', 'DSA lab'). Omit for overall + all subjects.",
    )


class ExamTimetableInput(BaseModel):
    session: str | None = Field(
        default=None,
        description="Optional academic session label (defaults to 'Jul 2026 - Dec 2026 (3rd Semester)').",
    )
    exam_name: str | None = Field(
        default=None,
        description="Optional exam name such as 'END TERM' or 'INTERNAL EXAM' (defaults to 'END TERM').",
    )


class BacklogInput(BaseModel):
    semester: int | None = Field(
        default=None,
        ge=1,
        le=8,
        description="Optional past semester number to inspect backlog courses for (defaults to 2).",
    )


class TeacherAssignmentsInput(BaseModel):
    unchecked_only: bool = Field(
        default=False,
        description="Set True when the teacher asks for pending checks / ungraded / unchecked submissions.",
    )
    course_hint: str | None = Field(
        default=None,
        description="Optional course code, name, or alias filter (e.g. 'DSA lab', 'OS').",
    )


class CreateCourseAssignmentInput(BaseModel):
    course_code: str = Field(
        description="Course code or unambiguous course name/alias taught by this teacher (e.g. 'DSA lab' or 'BCO008B').",
    )
    title: str = Field(
        default="",
        description="Assignment title (e.g. 'DSA Lab Assignment 2'). Leave empty to use '<Course Name> Assignment'.",
    )
    due_date_iso: str = Field(
        description="Target due date in ISO YYYY-MM-DD format (or explicit date string) — must be today or in the future.",
    )
    max_marks: float = Field(
        default=20.0,
        gt=0,
        le=100,
        description="Maximum marks for the assignment (defaults to 20.0).",
    )


class UpcomingHolidaysInput(BaseModel):
    limit: int = Field(
        default=5,
        ge=1,
        le=25,
        description="Maximum number of upcoming holidays to return.",
    )


class FacultyScreenGuideInput(BaseModel):
    topic: str = Field(
        description=(
            "Faculty ERP feature or screen keyword: e.g. 'OBE', 'question-wise', "
            "'mark entry', 'attendance', 'attendance register', 'teaching plan', "
            "'extra class', 'leave', 'payslip', or 'assignment'."
        ),
    )


# ============================================================================
# Shared execution & data-hygiene helpers
# ============================================================================


def _execute_cached_or_live(
    *,
    auth_ctx: AuthContext,
    tool_name: str,
    cacheable: bool,
    kwargs: dict[str, Any],
    fn: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Run a tool function with 30s per-user read caching and turn activity logging."""
    log = get_turn_log()
    user_key = auth_ctx.cache_user_key
    t0 = time.perf_counter()

    if log:
        log.tool_call(
            tool_name,
            kwargs or None,
            parallel_batch=current_parallel_batch(),
        )

    if cacheable:
        hit, cached_val = get_cached_tool_result(user_key, tool_name, kwargs=kwargs)
        if hit:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if log:
                log.tool_response(
                    tool_name,
                    cached_val,
                    cached=True,
                    elapsed_ms=elapsed_ms,
                )
            return cached_val

    try:
        result = fn()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        error_payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if log:
            log.tool_response(
                tool_name,
                error_payload,
                cached=False,
                elapsed_ms=elapsed_ms,
            )
        return error_payload

    if cacheable:
        set_cached_tool_result(user_key, tool_name, result, kwargs=kwargs)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if log:
        log.tool_response(
            tool_name,
            result,
            cached=False,
            elapsed_ms=elapsed_ms,
        )
    return result


def _enrich_student_assignments(
    raw_rows: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Apply `effective_status` and human-readable IST timestamps to assignment rows."""
    ref = now or datetime.now(IST)
    enriched: list[dict[str, Any]] = []
    for row in raw_rows:
        due_iso = row.get("due_date")
        eff_status = compute_effective_assignment_status(
            row.get("status"),
            due_iso,
            now=ref,
        )
        submitted_iso = row.get("submitted_at")
        enriched.append(
            {
                "id": str(row.get("id") or ""),
                "course_code": row.get("course_code"),
                "course_name": row.get("course_name"),
                "title": row.get("title"),
                "due_date": due_iso,
                "due_date_iso": due_iso,
                "due_date_ist": format_ist_datetime(due_iso) if due_iso else "",
                "raw_status": row.get("status") or "pending",
                "status": eff_status,
                "effective_status": eff_status,
                "marks_obtained": row.get("marks_obtained"),
                "max_marks": row.get("max_marks"),
                "submitted_at_iso": submitted_iso,
                "submitted_at_ist": format_ist_datetime(submitted_iso) if submitted_iso else None,
            }
        )
    return enriched


def _filter_assignments(
    rows: list[dict[str, Any]],
    *,
    status_filter: str | None = None,
    course_hint: str | None = None,
    assignment_number: int | None = None,
) -> list[dict[str, Any]]:
    filtered = list(rows)

    if status_filter:
        sf = status_filter.strip().casefold()
        if sf == "submitted":
            filtered = [
                r for r in filtered if r.get("effective_status") in {"submitted", "late"}
            ]
        elif sf in {"pending", "overdue"}:
            filtered = [r for r in filtered if r.get("effective_status") == sf]

    if course_hint and course_hint.strip():
        hint = course_hint.strip()
        matched = find_assignment_matches(
            hint,
            filtered,
            assignment_number=assignment_number,
        )
        if matched:
            filtered = matched
        else:
            best_course, _cands = match_course_from_list(
                hint,
                filtered,
                code_key="course_code",
                name_key="course_name",
            )
            if best_course is not None:
                code = best_course.get("course_code")
                filtered = [r for r in filtered if r.get("course_code") == code]
            else:
                norm_hint = normalize_alnum(normalize_chat_text(hint))
                expanded = expand_course_aliases(hint)
                filtered = [
                    r
                    for r in filtered
                    if (
                        has_phrase(norm_hint, normalize_alnum(str(r.get("course_code") or "")))
                        or any(
                            term in normalize_alnum(str(r.get("course_name") or ""))
                            for term in expanded
                        )
                    )
                ]
                if assignment_number is not None and filtered:
                    filtered = find_assignment_matches(
                        "",
                        filtered,
                        assignment_number=assignment_number,
                    )
    elif assignment_number is not None:
        filtered = find_assignment_matches(
            "",
            filtered,
            assignment_number=assignment_number,
        )

    return filtered


# ============================================================================
# Student Tool Factories
# ============================================================================


def _build_student_tool_factories(
    auth_ctx: AuthContext,
    side_effects: TurnSideEffects,
) -> dict[str, Callable[[], StructuredTool]]:
    if not auth_ctx.student_id or auth_ctx.semester is None:
        raise ValueError("Student AuthContext requires student_id and semester")

    student_id = auth_ctx.student_id
    default_semester = int(auth_ctx.semester)
    user_key = auth_ctx.cache_user_key

    def make_list_my_courses() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                courses = load_student_courses(student_id, default_semester)[:MAX_TOOL_ITEMS]
                return {
                    "semester": default_semester,
                    "count": len(courses),
                    "courses": courses,
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="list_my_courses",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="list_my_courses",
            description=(
                "List courses and faculty instructors the signed-in student is "
                "enrolled in for their current semester."
            ),
            args_schema=EmptyToolInput,
        )

    def make_list_assignments() -> StructuredTool:
        def _run(
            status_filter: Literal["pending", "overdue", "submitted"] | None = None,
            course_hint: str | None = None,
            assignment_number: int | None = None,
        ) -> dict[str, Any]:
            call_kwargs = {
                "status_filter": status_filter,
                "course_hint": course_hint,
                "assignment_number": assignment_number,
            }

            def _fetch() -> dict[str, Any]:
                raw = list_current_assignments(student_id, default_semester)
                enriched = _enrich_student_assignments(raw)
                counts = {
                    "total": len(enriched),
                    "pending": sum(1 for r in enriched if r["effective_status"] == "pending"),
                    "overdue": sum(1 for r in enriched if r["effective_status"] == "overdue"),
                    "submitted": sum(
                        1 for r in enriched if r["effective_status"] in {"submitted", "late"}
                    ),
                }
                filtered = _filter_assignments(
                    enriched,
                    status_filter=status_filter,
                    course_hint=course_hint,
                    assignment_number=assignment_number,
                )
                capped = filtered[:MAX_TOOL_ITEMS]
                return {
                    "semester": default_semester,
                    "filters": call_kwargs,
                    "overall_counts": counts,
                    "matched_count": len(filtered),
                    "assignments": capped,
                }

            result = _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="list_assignments",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )
            matched_rows = result.get("assignments") or []
            if len(matched_rows) == 1:
                only = matched_rows[0]
                update_session_slots(
                    user_key,
                    course_code=only.get("course_code"),
                    course_name=only.get("course_name"),
                    assignment_id=only.get("id"),
                )
            return result

        return StructuredTool.from_function(
            func=_run,
            name="list_assignments",
            description=(
                "List assignments for the student's current semester with deadlines "
                "(both ISO and IST) and effective status ('pending', 'overdue', 'submitted'). "
                "Supports filtering by status_filter, course_hint, or assignment_number."
            ),
            args_schema=ListAssignmentsInput,
        )

    def make_prepare_assignment_upload() -> StructuredTool:
        def _run(
            course_hint: str | None = None,
            assignment_number: int | None = None,
        ) -> dict[str, Any]:
            call_kwargs = {
                "course_hint": course_hint,
                "assignment_number": assignment_number,
            }

            # Do NOT cache side-effectful upload preparation across turns so
            # `turn_side_effects.action` always reflects live DB state.
            def _prepare() -> dict[str, Any]:
                raw = list_current_assignments(student_id, default_semester)
                enriched = _enrich_student_assignments(raw)
                if not enriched:
                    return {
                        "ready_for_upload": False,
                        "status": "no_assignments_in_semester",
                        "semester": default_semester,
                        "candidates": [],
                    }

                hint = (course_hint or "").strip()
                if not hint and assignment_number is None:
                    return {
                        "ready_for_upload": False,
                        "status": "missing_course_or_assignment_hint",
                        "reason": (
                            "Ask the student which subject/course code or assignment title "
                            "they want to upload."
                        ),
                        "candidates": enriched[:MAX_TOOL_ITEMS],
                    }

                query_parts: list[str] = []
                if hint:
                    query_parts.append(hint)
                if assignment_number is not None:
                    query_parts.append(f"assignment {assignment_number}")
                combined_query = " ".join(query_parts).strip()

                matches = find_assignment_matches(
                    combined_query,
                    enriched,
                    assignment_number=assignment_number,
                )
                if not matches and hint:
                    matches = _filter_assignments(
                        enriched,
                        course_hint=hint,
                        assignment_number=assignment_number,
                    )

                if len(matches) == 1:
                    matched_row = matches[0]
                    upload_assignment_payload = {
                        "id": matched_row["id"],
                        "course_code": matched_row["course_code"],
                        "course_name": matched_row["course_name"],
                        "title": matched_row["title"],
                        "due_date": matched_row["due_date_iso"],
                        "due_date_ist": matched_row["due_date_ist"],
                        "status": matched_row["effective_status"],
                    }
                    side_effects.action = {
                        "type": "upload_confirmation",
                        "assignment": upload_assignment_payload,
                    }
                    side_effects.kind_override = "upload"
                    side_effects.title_override = "Ready for your file"
                    update_session_slots(
                        user_key,
                        course_code=matched_row.get("course_code"),
                        course_name=matched_row.get("course_name"),
                        assignment_id=matched_row.get("id"),
                    )
                    return {
                        "ready_for_upload": True,
                        "status": "upload_picker_attached",
                        "note": (
                            "The PDF/DOCX file picker has been attached below your reply. "
                            "Instruct the student to select their PDF or DOCX file (max 3 MB) "
                            "and click Confirm upload."
                        ),
                        "assignment": matched_row,
                    }

                if len(matches) > 1:
                    return {
                        "ready_for_upload": False,
                        "status": "multiple_matches",
                        "reason": (
                            "Multiple assignments matched. Ask the student to specify "
                            "the assignment number or exact title."
                        ),
                        "matched_count": len(matches),
                        "candidates": matches[:MAX_TOOL_ITEMS],
                    }

                return {
                    "ready_for_upload": False,
                    "status": "no_match",
                    "reason": (
                        "No enrolled theory assignment matched that hint. Show available "
                        "courses/assignments so the student can pick one."
                    ),
                    "candidates": enriched[:MAX_TOOL_ITEMS],
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="prepare_assignment_upload",
                cacheable=False,
                kwargs=call_kwargs,
                fn=_prepare,
            )

        return StructuredTool.from_function(
            func=_run,
            name="prepare_assignment_upload",
            description=(
                "Prepare a guided PDF/DOCX assignment file upload in chat. Call this "
                "ONLY when the student wants to upload or submit an assignment file."
            ),
            args_schema=PrepareUploadInput,
        )

    def make_get_attendance() -> StructuredTool:
        def _run(subject_hint: str | None = None) -> dict[str, Any]:
            clean_hint = (subject_hint or "").strip() or None
            call_kwargs = {"subject_hint": clean_hint}

            def _fetch() -> dict[str, Any]:
                summary = get_attendance_summary(student_id)
                if clean_hint:
                    summary = filter_attendance_subjects(summary, clean_hint)
                subjects = list(summary.get("subjects") or [])[:MAX_TOOL_ITEMS]
                return {
                    **summary,
                    "subjects": subjects,
                }

            result = _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_attendance",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )
            subjects = result.get("subjects") or []
            if result.get("filter_matched") and len(subjects) == 1:
                update_session_slots(
                    user_key,
                    course_code=subjects[0].get("code"),
                    course_name=subjects[0].get("name"),
                )
            return result

        return StructuredTool.from_function(
            func=_run,
            name="get_attendance",
            description=(
                "Fetch live class attendance summary (overall percentage, present/absent/OD "
                "counts, and subject-wise breakdown). Pass subject_hint to filter to a specific course."
            ),
            args_schema=AttendanceInput,
        )

    def make_get_results() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                payload = get_results_summary(student_id)
                return {
                    "full_name": payload.get("full_name"),
                    "semester": payload.get("semester"),
                    "branch": payload.get("branch"),
                    "results": list(payload.get("results") or [])[:MAX_TOOL_ITEMS],
                    "internal_marks": list(payload.get("internal_marks") or [])[:MAX_TOOL_ITEMS],
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_results",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_results",
            description=(
                "Fetch the student's semester exam results (SGPA, earned/registered credits, "
                "failed courses) and current-session internal mark subjects."
            ),
            args_schema=EmptyToolInput,
        )

    def make_get_fees() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                payload = get_fees_summary(student_id)
                return {
                    "full_name": payload.get("full_name"),
                    "semester": payload.get("semester"),
                    "branch": payload.get("branch"),
                    "total_outstanding": payload.get("total_outstanding", 0.0),
                    "currency": "INR",
                    "fees": list(payload.get("fees") or [])[:MAX_TOOL_ITEMS],
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_fees",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_fees",
            description=(
                "Fetch the student's read-only academic fee ledger (applied, paid, "
                "outstanding amounts, and receipt numbers)."
            ),
            args_schema=EmptyToolInput,
        )

    def make_get_exam_timetable() -> StructuredTool:
        def _run(
            session: str | None = None,
            exam_name: str | None = None,
        ) -> dict[str, Any]:
            clean_session = (session or "").strip() or None
            clean_exam = (exam_name or "").strip() or None
            call_kwargs = {"session": clean_session, "exam_name": clean_exam}

            def _fetch() -> dict[str, Any]:
                payload = load_exam_timetable_data(
                    student_id,
                    session=clean_session,
                    exam_name=clean_exam,
                )
                meta = get_exam_meta()
                rows = list(payload.get("rows") or [])[:MAX_TOOL_ITEMS]
                return {
                    "session": payload.get("session"),
                    "exam_name": payload.get("exam_name"),
                    "paper_count": len(rows),
                    "rows": rows,
                    "available_sessions": meta.get("sessions", []),
                    "available_exam_names": meta.get("exam_names", []),
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_exam_timetable",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_exam_timetable",
            description=(
                "Fetch the student's examination timetable / exam schedule (dates, "
                "time slots, course codes, and course names)."
            ),
            args_schema=ExamTimetableInput,
        )

    def make_get_admit_card() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                payload = load_admit_card_data(student_id)
                card = dict(payload.get("admit_card") or payload)
                papers = list(card.get("papers") or [])[:MAX_TOOL_ITEMS]
                card["papers"] = papers
                return {"admit_card": card}

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_admit_card",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_admit_card",
            description=(
                "Fetch the student's examination admit card / hall ticket details "
                "(center, session, exam name, scheduled papers, and hall instructions)."
            ),
            args_schema=EmptyToolInput,
        )

    def make_get_backlog_courses() -> StructuredTool:
        def _run(semester: int | None = None) -> dict[str, Any]:
            call_kwargs = {"semester": semester}

            def _fetch() -> dict[str, Any]:
                payload = load_backlog_data(student_id, semester=semester)
                courses = list(payload.get("courses") or [])[:MAX_TOOL_ITEMS]
                return {
                    **payload,
                    "courses": courses,
                    "read_only_notice": (
                        "Backlog registration and fee payment can only be completed "
                        "in the Examination / Pay Online portal UI."
                    ),
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_backlog_courses",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_backlog_courses",
            description=(
                "Fetch the student's read-only backlog / arrear course list and "
                "backlog exam fee summary for a given semester (defaults to semester 2)."
            ),
            args_schema=BacklogInput,
        )

    def make_get_student_profile_info() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                profile = get_student_profile(student_id)
                personal = dict(profile.get("personal") or {})
                # Keep profile compact and omit unnecessary bulky sub-arrays
                return {
                    "full_name": profile.get("full_name"),
                    "rrn": profile.get("rrn"),
                    "enrollment_no": profile.get("enrollment_no"),
                    "email": profile.get("email"),
                    "mobile": profile.get("mobile"),
                    "semester": profile.get("semester"),
                    "branch": profile.get("branch"),
                    "degree_branch": profile.get("degree_branch"),
                    "school": profile.get("school"),
                    "session": profile.get("session"),
                    "admission_batch": profile.get("admission_batch"),
                    "admission_type": profile.get("admission_type"),
                    "admission_status": profile.get("admission_status"),
                    "dob": personal.get("dob"),
                    "gender": personal.get("gender"),
                    "category": personal.get("category"),
                    "mentor": profile.get("mentor"),
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_student_profile_info",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_student_profile_info",
            description=(
                "Fetch the student's personal & academic profile details (name, "
                "RRN/enrollment number, current semester, session, branch, school, "
                "DOB, admission batch, and faculty mentor)."
            ),
            args_schema=EmptyToolInput,
        )

    return {
        "list_my_courses": make_list_my_courses,
        "list_assignments": make_list_assignments,
        "prepare_assignment_upload": make_prepare_assignment_upload,
        "get_attendance": make_get_attendance,
        "get_results": make_get_results,
        "get_fees": make_get_fees,
        "get_exam_timetable": make_get_exam_timetable,
        "get_admit_card": make_get_admit_card,
        "get_backlog_courses": make_get_backlog_courses,
        "get_student_profile_info": make_get_student_profile_info,
    }


# ============================================================================
# Faculty Tool Factories
# ============================================================================


def _build_faculty_tool_factories(
    auth_ctx: AuthContext,
    side_effects: TurnSideEffects,
) -> dict[str, Callable[[], StructuredTool]]:
    if not auth_ctx.teacher_id:
        raise ValueError("Faculty AuthContext requires teacher_id")

    teacher_id = auth_ctx.teacher_id
    teacher_dict = dict(auth_ctx.teacher or {})
    user_key = auth_ctx.cache_user_key

    def make_get_teacher_courses() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                courses = teacher_courses(teacher_id)[:MAX_TOOL_ITEMS]
                return {
                    "teacher_name": teacher_dict.get("full_name"),
                    "department": teacher_dict.get("department"),
                    "course_count": len(courses),
                    "courses": courses,
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_teacher_courses",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_teacher_courses",
            description=(
                "Fetch the courses taught by the signed-in teacher, including "
                "course codes, semesters, enrolled student counts, and assignment counts."
            ),
            args_schema=EmptyToolInput,
        )

    def make_get_teacher_assignments() -> StructuredTool:
        def _run(
            unchecked_only: bool = False,
            course_hint: str | None = None,
        ) -> dict[str, Any]:
            clean_hint = (course_hint or "").strip() or None
            call_kwargs = {
                "unchecked_only": bool(unchecked_only),
                "course_hint": clean_hint,
            }

            def _fetch() -> dict[str, Any]:
                rows = teacher_assignments(
                    teacher_id,
                    unchecked_only=bool(unchecked_only),
                    course_hint=clean_hint,
                    max_items=MAX_TOOL_ITEMS,
                )
                total_unchecked = sum(int(r.get("unchecked") or 0) for r in rows)
                return {
                    "unchecked_only": bool(unchecked_only),
                    "course_hint": clean_hint,
                    "assignment_count": len(rows),
                    "total_unchecked_submissions": total_unchecked,
                    "assignments": rows,
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_teacher_assignments",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_teacher_assignments",
            description=(
                "Fetch assignments across courses taught by this teacher, including "
                "due dates (ISO & IST), total replies, and unchecked submission counts. "
                "Set unchecked_only=True for pending grading checks."
            ),
            args_schema=TeacherAssignmentsInput,
        )

    def make_create_course_assignment() -> StructuredTool:
        def _run(
            course_code: str,
            due_date_iso: str,
            title: str = "",
            max_marks: float = 20.0,
        ) -> dict[str, Any]:
            call_kwargs = {
                "course_code": course_code,
                "title": title,
                "due_date_iso": due_date_iso,
                "max_marks": max_marks,
            }

            def _mutate() -> dict[str, Any]:
                return create_faculty_assignment(
                    teacher_id,
                    course_code=course_code,
                    title=title,
                    due_date_iso=due_date_iso,
                    max_marks=max_marks,
                    user_id=auth_ctx.user_id,
                )

            result = _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="create_course_assignment",
                cacheable=False,
                kwargs=call_kwargs,
                fn=_mutate,
            )
            if result.get("created") and isinstance(result.get("assignment"), dict):
                created = result["assignment"]
                side_effects.kind_override = "faculty_assignment_created"
                side_effects.title_override = "Assignment created"
                update_session_slots(
                    user_key,
                    course_code=created.get("course_code"),
                    course_name=created.get("course_name"),
                    assignment_id=created.get("id"),
                )
            return result

        return StructuredTool.from_function(
            func=_run,
            name="create_course_assignment",
            description=(
                "Create and publish a new assignment for one of this teacher's courses. "
                "Call ONLY when the teacher explicitly asks to create/add/publish an assignment "
                "and both the target course and a current/future due date are known. If ambiguous, "
                "first call get_teacher_courses and ask the teacher to confirm."
            ),
            args_schema=CreateCourseAssignmentInput,
        )

    def make_get_teacher_leave_balance() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                balances = teacher_leave(teacher_dict, teacher_id)[:MAX_TOOL_ITEMS]
                return {
                    "employee_id": teacher_dict.get("employee_id"),
                    "full_name": teacher_dict.get("full_name"),
                    "department": teacher_dict.get("department"),
                    "balances": balances,
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_teacher_leave_balance",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_teacher_leave_balance",
            description=(
                "Fetch the teacher's Establishment leave card balances (Casual, "
                "Earned, Medical, Vacation: opening, credit, taken, remaining balance)."
            ),
            args_schema=EmptyToolInput,
        )

    def make_get_teacher_payslip() -> StructuredTool:
        def _run() -> dict[str, Any]:
            def _fetch() -> dict[str, Any]:
                slip = teacher_payslip(teacher_dict)
                return {"currency": "INR", **slip}

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_teacher_payslip",
                cacheable=True,
                kwargs={},
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_teacher_payslip",
            description=(
                "Fetch the teacher's own current-month salary payslip summary "
                "(earnings, deductions, gross pay, total deductions, and net take-home pay)."
            ),
            args_schema=EmptyToolInput,
        )

    def make_get_upcoming_holidays() -> StructuredTool:
        def _run(limit: int = 5) -> dict[str, Any]:
            capped = max(1, min(int(limit or 5), MAX_TOOL_ITEMS))
            call_kwargs = {"limit": capped}

            def _fetch() -> dict[str, Any]:
                rows = upcoming_holidays(limit=capped)
                return {
                    "count": len(rows),
                    "holidays": rows,
                }

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_upcoming_holidays",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_upcoming_holidays",
            description="Fetch upcoming academic calendar holidays.",
            args_schema=UpcomingHolidaysInput,
        )

    def make_get_faculty_screen_guide() -> StructuredTool:
        def _run(topic: str) -> dict[str, Any]:
            clean_topic = (topic or "").strip()
            call_kwargs = {"topic": clean_topic}

            def _fetch() -> dict[str, Any]:
                return get_faculty_navigation_guide(clean_topic)

            return _execute_cached_or_live(
                auth_ctx=auth_ctx,
                tool_name="get_faculty_screen_guide",
                cacheable=True,
                kwargs=call_kwargs,
                fn=_fetch,
            )

        return StructuredTool.from_function(
            func=_run,
            name="get_faculty_screen_guide",
            description=(
                "Look up where a faculty portal feature or screen lives (OBE, "
                "question-wise mark entry, exam mark entry, attendance, attendance register, "
                "teaching plan, extra class, leave card, payslip, or LMS assignments)."
            ),
            args_schema=FacultyScreenGuideInput,
        )

    return {
        "get_teacher_courses": make_get_teacher_courses,
        "get_teacher_assignments": make_get_teacher_assignments,
        "create_course_assignment": make_create_course_assignment,
        "get_teacher_leave_balance": make_get_teacher_leave_balance,
        "get_teacher_payslip": make_get_teacher_payslip,
        "get_upcoming_holidays": make_get_upcoming_holidays,
        "get_faculty_screen_guide": make_get_faculty_screen_guide,
    }


# ============================================================================
# Deterministic Category -> Tool Name Registries
# ============================================================================

STUDENT_CATEGORY_TOOL_MAP: dict[IntentCategory, tuple[str, ...]] = {
    IntentCategory.LMS: (
        "list_my_courses",
        "list_assignments",
        "prepare_assignment_upload",
    ),
    IntentCategory.ACADEMIC: (
        "get_attendance",
        "get_results",
        "get_fees",
    ),
    IntentCategory.EXAMINATION: (
        "get_exam_timetable",
        "get_admit_card",
        "get_backlog_courses",
    ),
    IntentCategory.PERSONAL_INFO: (
        "get_student_profile_info",
    ),
    # Planning expands deterministically to LMS (list_assignments) + Academic (get_attendance)
    IntentCategory.PLANNING: (
        "list_assignments",
        "get_attendance",
    ),
}

FACULTY_CATEGORY_TOOL_MAP: dict[IntentCategory, tuple[str, ...]] = {
    IntentCategory.FACULTY_LMS_READ: (
        "get_teacher_courses",
        "get_teacher_assignments",
    ),
    IntentCategory.FACULTY_LMS_WRITE: (
        "get_teacher_courses",
        "create_course_assignment",
    ),
    IntentCategory.FACULTY_HR_PAYROLL: (
        "get_teacher_leave_balance",
        "get_teacher_payslip",
        "get_upcoming_holidays",
    ),
    IntentCategory.FACULTY_NAVIGATION: (
        "get_faculty_screen_guide",
    ),
    # Planning expands deterministically to courses + assignments + holidays
    IntentCategory.PLANNING: (
        "get_teacher_courses",
        "get_teacher_assignments",
        "get_upcoming_holidays",
    ),
}


def get_tool_names_for_intents(
    role: Literal["student", "teacher"],
    all_classified_intents: list[IntentCategory],
) -> list[str]:
    """Pure deterministic lookup of deduplicated tool names for `(role, intents)`."""
    category_map = (
        FACULTY_CATEGORY_TOOL_MAP
        if role == "teacher"
        else STUDENT_CATEGORY_TOOL_MAP
    )
    resolved_names: list[str] = []
    seen: set[str] = set()
    for intent in all_classified_intents:
        for tool_name in category_map.get(intent, ()):
            if tool_name not in seen:
                seen.add(tool_name)
                resolved_names.append(tool_name)
    return resolved_names


def get_tools_for_intents(
    role: Literal["student", "teacher"],
    all_classified_intents: list[IntentCategory],
    auth_ctx: AuthContext,
    turn_side_effects: TurnSideEffects | None = None,
) -> list[StructuredTool]:
    """Deterministically resolve user-scoped `StructuredTool`s for Stage 2/3.

    Returns `[]` for non-tool intents (`greeting_help`, `forbidden_write`,
    `out_of_scope`), and logs resolved tool names to `chat_activity_log` when
    inside an active turn.
    """
    side_effects = turn_side_effects if turn_side_effects is not None else TurnSideEffects()
    tool_names = get_tool_names_for_intents(role, all_classified_intents)

    log = get_turn_log()
    if log:
        log.resolved_tools(tool_names)

    if not tool_names:
        return []

    factories = (
        _build_faculty_tool_factories(auth_ctx, side_effects)
        if role == "teacher"
        else _build_student_tool_factories(auth_ctx, side_effects)
    )
    return [factories[name]() for name in tool_names if name in factories]
