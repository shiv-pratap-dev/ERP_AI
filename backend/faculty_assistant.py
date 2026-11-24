"""Pure-JSON faculty ERP data & action helpers for Stage 2/3 tool calling.

Exposes only structured JSON functions (zero markdown builders or sequential
regex routers):
- `teacher_courses`
- `teacher_assignments`
- `teacher_leave`
- `teacher_payslip`
- `upcoming_holidays`
- `get_faculty_navigation_guide`
- `create_faculty_assignment`
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from database import get_connection
from erp_ai.chat.cache import invalidate_student_cache, invalidate_teacher_cache
from erp_ai.chat.normalizer import (
    IST,
    expand_course_aliases,
    format_ist_datetime,
    has_phrase,
    match_course_from_list,
    normalize_alnum,
    normalize_chat_text,
    parse_iso_or_natural_date,
)
from establishment_api import (
    HOLIDAYS_2026,
    _balance_rows,
    _seed_teacher,
    ensure_establishment_schema,
)
from payroll_api import _salary_for

FACULTY_SCREEN_GUIDES: dict[str, dict[str, str]] = {
    "question_wise": {
        "topic": "Question Wise Mark Entry",
        "menu_path": "OBE -> Question Wise Mark Entry",
        "url_path": "/faculty/OBE/question-wise.html",
        "instructions": (
            "Select the academic session and scheme, open the subject, "
            "then enter Part A / B / C question-wise marks and save."
        ),
    },
    "obe": {
        "topic": "Outcome Based Education (OBE)",
        "menu_path": "OBE -> Portal / Question Wise Mark Entry / Exam Question Paper / Mark Entry Report",
        "url_path": "/faculty/OBE/portal.html",
        "instructions": (
            "Use the OBE menu to manage CO/PO mappings, Exam Question Papers, "
            "Question Wise Mark Entry, and Mark Entry Reports."
        ),
    },
    "mark_entry": {
        "topic": "Examination Mark Entry",
        "menu_path": "Examination -> Mark Entry",
        "url_path": "/faculty/Examination/mark-entry.html",
        "instructions": (
            "Choose Session, Exam Name, Course, and Section, click Show, "
            "then enter or lock student exam marks."
        ),
    },
    "attendance_register": {
        "topic": "Attendance Register Report",
        "menu_path": "Academic -> Attendance Register Report",
        "url_path": "/faculty/Academic/attendance-register.html",
        "instructions": (
            "Select course, section, and date range to view or export the "
            "cumulative class attendance register."
        ),
    },
    "attendance": {
        "topic": "Attendance By Faculty",
        "menu_path": "Academic -> Attendance By Faculty",
        "url_path": "/faculty/Academic/attendance.html",
        "instructions": (
            "Pick the class session slot and date, mark students Present (P), "
            "Absent (A), or On Duty (OD), and submit."
        ),
    },
    "teaching_plan": {
        "topic": "Teaching Plan Entry",
        "menu_path": "Academic -> Teaching Plan Entry",
        "url_path": "/faculty/Academic/teaching-plan.html",
        "instructions": (
            "Select Core or Elective plan type, course, and unit topics to "
            "record proposed and conducted lecture dates."
        ),
    },
    "extra_class": {
        "topic": "Extra Class Time Table",
        "menu_path": "Academic -> Extra Class Time Table",
        "url_path": "/faculty/Academic/extra-class.html",
        "instructions": (
            "Schedule or manage makeup/extra class slots, rooms, and date ranges "
            "for your assigned courses."
        ),
    },
    "payslip": {
        "topic": "Employee PaySlip",
        "menu_path": "Payroll -> Reports -> Employee PaySlip",
        "url_path": "/faculty/Payroll/employee-payslip.html",
        "instructions": (
            "Choose Month/Year and report mode (Single Payslip, 3-Month, 6-Month, "
            "or Increment) to view or print your salary statement."
        ),
    },
    "leave": {
        "topic": "Employee Leave Card & Establishment",
        "menu_path": "Establishment -> Transactions -> Employee Leave Card",
        "url_path": "/faculty/Establishment/leave-card.html",
        "instructions": (
            "View Casual, Earned, Medical, and Vacation leave balances, apply for "
            "leave, or navigate to OD Application, Comp-Off, and Service Book."
        ),
    },
    "assignment": {
        "topic": "LMS Assignment Management",
        "menu_path": "LMS -> Select Course -> Create Assignment / Check Student Assignment",
        "url_path": "/faculty/LMS/select-course.html",
        "instructions": (
            "Select a course to publish assignments with attachments or grade "
            "student submissions in Check Student Assignment."
        ),
    },
}


def teacher_courses(teacher_id: str) -> list[dict[str, Any]]:
    """Return courses taught by `teacher_id` with enrolled student and assignment counts."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.course_code, c.course_name, c.semester,
                   COALESCE(c.subject_type, 'Theory') AS subject_type,
                   COUNT(DISTINCT e.student_id) AS student_count,
                   COUNT(DISTINCT a.id) AS assignment_count
            FROM courses c
            LEFT JOIN enrollments e ON e.course_id = c.id
            LEFT JOIN assignments a ON a.course_id = c.id
            WHERE c.teacher_id = %s
            GROUP BY c.id, c.course_code, c.course_name, c.semester, c.subject_type
            ORDER BY c.course_code
            """,
            (teacher_id,),
        ).fetchall()
    return [
        {
            "id": str(row[0]),
            "course_code": row[1],
            "course_name": row[2],
            "semester": int(row[3]),
            "subject_type": row[4],
            "student_count": int(row[5] or 0),
            "assignment_count": int(row[6] or 0),
        }
        for row in rows
    ]


def teacher_assignments(
    teacher_id: str,
    *,
    unchecked_only: bool = False,
    course_hint: str | None = None,
    max_items: int = 25,
) -> list[dict[str, Any]]:
    """Return pure-JSON assignments for `teacher_id` with submission & unchecked counts."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, c.course_code, c.course_name, a.title, a.due_date, a.max_marks,
                   COUNT(s.id) AS replies,
                   COUNT(s.id) FILTER (WHERE s.marks_obtained IS NULL) AS unchecked
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE c.teacher_id = %s
            GROUP BY a.id, c.course_code, c.course_name, a.title, a.due_date, a.max_marks
            ORDER BY a.due_date ASC, c.course_code ASC
            """,
            (teacher_id,),
        ).fetchall()

    now_ist = datetime.now(IST)
    items: list[dict[str, Any]] = []
    for row in rows:
        due = row[4]
        due_iso = due.isoformat() if due else None
        due_ist = format_ist_datetime(due) if due else ""
        due_aware = (
            due.replace(tzinfo=timezone.utc).astimezone(IST)
            if due and due.tzinfo is None
            else (due.astimezone(IST) if due else None)
        )
        unchecked = int(row[7] or 0)
        if unchecked_only and unchecked <= 0:
            continue
        items.append(
            {
                "id": str(row[0]),
                "course_code": row[1],
                "course_name": row[2],
                "title": row[3],
                "due_date_iso": due_iso,
                "due_date_ist": due_ist,
                "due_date": due_ist,
                "is_active": bool(due_aware and due_aware > now_ist),
                "max_marks": float(row[5] or 0),
                "replies": int(row[6] or 0),
                "unchecked": unchecked,
            }
        )

    if course_hint and course_hint.strip() and items:
        hint = course_hint.strip()
        best_course, _candidates = match_course_from_list(
            hint,
            items,
            code_key="course_code",
            name_key="course_name",
        )
        if best_course is not None:
            target_code = best_course.get("course_code")
            items = [item for item in items if item.get("course_code") == target_code]
        else:
            norm_hint = normalize_alnum(normalize_chat_text(hint))
            expanded = expand_course_aliases(hint)
            filtered = [
                item
                for item in items
                if (
                    has_phrase(norm_hint, normalize_alnum(str(item.get("course_code") or "")))
                    or any(
                        term in normalize_alnum(str(item.get("course_name") or ""))
                        for term in expanded
                    )
                )
            ]
            items = filtered

    return items[: max(1, max_items)]


def teacher_leave(teacher: dict[str, Any], teacher_id: str) -> list[dict[str, Any]]:
    """Return leave card balances for `teacher_id` as pure JSON."""
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        _seed_teacher(conn, teacher_id, teacher.get("department") or "Computer Science")
        balances = _balance_rows(conn, teacher_id)
        conn.commit()
    return balances


def teacher_payslip(teacher: dict[str, Any]) -> dict[str, Any]:
    """Return current-month salary summary for `teacher` without sensitive IDs (PAN/UAN/bank)."""
    month = date.today().replace(day=1)
    slip = _salary_for(
        teacher.get("employee_id") or "",
        teacher.get("full_name") or "",
        teacher.get("department") or "",
        month,
    )
    return {
        "employee_id": slip["employee_id"],
        "employee_name": slip["employee_name"],
        "department": slip["department"],
        "designation": slip["designation"],
        "month_label": slip["month_label"],
        "month_value": slip["month_value"],
        "working_days": slip["working_days"],
        "paid_days": slip["paid_days"],
        "earnings": slip["earnings"],
        "deductions": slip["deductions"],
        "gross": slip["gross"],
        "total_deduction": slip["total_deduction"],
        "net": slip["net"],
    }


def upcoming_holidays(limit: int = 5) -> list[dict[str, str]]:
    """Return upcoming academic calendar holidays as pure JSON."""
    capped_limit = max(1, min(int(limit or 5), 25))
    today = date.today()
    rows = [
        {
            "date_iso": day.isoformat(),
            "date_ist": day.strftime("%d %b %Y"),
            "date": day.strftime("%d %b %Y"),
            "weekday": day.strftime("%A"),
            "name": name,
        }
        for day, name in HOLIDAYS_2026
        if day >= today
    ]
    return rows[:capped_limit]


def get_faculty_navigation_guide(topic: str) -> dict[str, Any]:
    """Resolve a faculty portal screen navigation guide by topic keyword."""
    normalized = normalize_alnum(normalize_chat_text(topic or ""))
    matched_key: str | None = None

    if "question" in normalized and ("wise" in normalized or "mark" in normalized or "obe" in normalized):
        matched_key = "question_wise"
    elif "obe" in normalized or "outcome" in normalized or "question paper" in normalized:
        matched_key = "obe"
    elif "mark entry" in normalized or ("exam" in normalized and "mark" in normalized):
        matched_key = "mark_entry"
    elif "register" in normalized and "attendance" in normalized:
        matched_key = "attendance_register"
    elif "teaching plan" in normalized or "lecture plan" in normalized:
        matched_key = "teaching_plan"
    elif "extra class" in normalized or "makeup class" in normalized:
        matched_key = "extra_class"
    elif "attendance" in normalized:
        matched_key = "attendance"
    elif any(k in normalized for k in ("payslip", "salary", "payroll", "pay slip")):
        matched_key = "payslip"
    elif any(k in normalized for k in ("leave", "od", "comp off", "compoff", "service book", "establishment")):
        matched_key = "leave"
    elif any(k in normalized for k in ("assignment", "lms", "check student", "grading")):
        matched_key = "assignment"

    if matched_key is not None:
        return {
            "matched": True,
            "query_topic": topic,
            "guide": FACULTY_SCREEN_GUIDES[matched_key],
        }

    return {
        "matched": False,
        "query_topic": topic,
        "available_screens": list(FACULTY_SCREEN_GUIDES.values()),
    }


def create_faculty_assignment(
    teacher_id: str,
    *,
    course_code: str,
    title: str,
    due_date_iso: str,
    max_marks: float = 20.0,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Create a new course assignment only when course & future due date are unambiguous.

    Never guesses a course or past due date; returns structured `status` +
    `candidates` so the LLM can ask the teacher to confirm when ambiguous.
    """
    from faculty_api import ensure_lms_schema

    courses = teacher_courses(teacher_id)
    if not courses:
        return {
            "created": False,
            "status": "no_courses_assigned",
            "reason": "Teacher has no assigned courses.",
            "candidates": [],
        }

    raw_course = (course_code or "").strip()
    if not raw_course:
        return {
            "created": False,
            "status": "ambiguous_course",
            "reason": "Course code or course name was not specified.",
            "candidates": courses[:10],
        }

    matched_course: dict[str, Any] | None = None
    exact_matches = [
        c
        for c in courses
        if str(c.get("course_code") or "").strip().upper() == raw_course.upper()
    ]
    if len(exact_matches) == 1:
        matched_course = exact_matches[0]
        candidates = exact_matches
    else:
        matched_course, candidates = match_course_from_list(
            raw_course,
            courses,
            code_key="course_code",
            name_key="course_name",
        )

    if matched_course is None:
        return {
            "created": False,
            "status": "ambiguous_course",
            "reason": (
                "Multiple courses matched that hint; ask the teacher to confirm the exact course code."
                if candidates
                else "No taught course matched that hint; ask the teacher to pick from their assigned courses."
            ),
            "requested_course": raw_course,
            "candidates": (candidates or courses)[:10],
        }

    parsed_day = parse_iso_or_natural_date(due_date_iso or "")
    today_ist = datetime.now(IST).date()
    if parsed_day is None:
        return {
            "created": False,
            "status": "missing_or_invalid_due_date",
            "reason": "A valid due date is required (e.g., YYYY-MM-DD or '27 Nov 2026').",
            "course": {
                "course_code": matched_course["course_code"],
                "course_name": matched_course["course_name"],
                "semester": matched_course["semester"],
            },
        }

    if parsed_day < today_ist:
        return {
            "created": False,
            "status": "past_due_date",
            "reason": (
                f"Due date {parsed_day.isoformat()} is in the past (today is {today_ist.isoformat()}). "
                "Ask the teacher to confirm a current or future due date."
            ),
            "course": {
                "course_code": matched_course["course_code"],
                "course_name": matched_course["course_name"],
                "semester": matched_course["semester"],
            },
            "requested_due_date_iso": parsed_day.isoformat(),
        }

    clean_title = (title or "").strip()
    if not clean_title:
        clean_title = f"{matched_course['course_name']} Assignment"
    clean_title = clean_title[:200]

    safe_marks = float(max_marks if max_marks and max_marks > 0 else 20.0)
    due_ist = datetime.combine(parsed_day, time(23, 59), tzinfo=IST)
    due_utc = due_ist.astimezone(timezone.utc)
    description = f"Created via ERP Assistant on {today_ist.isoformat()}."

    enrolled_student_ids: list[str] = []
    with get_connection() as conn:
        ensure_lms_schema(conn)
        row = conn.execute(
            """
            INSERT INTO assignments
                (course_id, created_by, title, description, due_date, max_marks)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, title, due_date, max_marks, created_at
            """,
            (
                matched_course["id"],
                teacher_id,
                clean_title,
                description,
                due_utc,
                safe_marks,
            ),
        ).fetchone()
        students = conn.execute(
            """
            SELECT s.id
            FROM students s
            JOIN enrollments e ON e.student_id = s.id
            WHERE e.course_id = %s
            """,
            (matched_course["id"],),
        ).fetchall()
        for (student_id,) in students:
            enrolled_student_ids.append(str(student_id))
            conn.execute(
                """
                INSERT INTO assignment_recipients (assignment_id, student_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (row[0], student_id),
            )
        conn.commit()

    invalidate_teacher_cache(teacher_id, user_id)
    for sid in enrolled_student_ids:
        invalidate_student_cache(sid)

    created_due = row[2]
    return {
        "created": True,
        "status": "created",
        "assignment": {
            "id": str(row[0]),
            "course_code": matched_course["course_code"],
            "course_name": matched_course["course_name"],
            "semester": matched_course["semester"],
            "title": row[1],
            "due_date_iso": created_due.isoformat() if created_due else due_ist.isoformat(),
            "due_date_ist": format_ist_datetime(created_due or due_ist),
            "max_marks": float(row[3]),
            "students_notified": len(enrolled_student_ids),
        },
    }
