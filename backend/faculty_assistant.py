"""Read-only answers for the faculty ERP assistant."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from database import get_connection
from establishment_api import (
    HOLIDAYS_2026,
    _balance_rows,
    _seed_teacher,
    ensure_establishment_schema,
)
from payroll_api import _salary_for


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_has_phrase(text, phrase) for phrase in phrases)


def _answer(message: str, *, title: str, kind: str = "help") -> dict[str, Any]:
    return {
        "message": message,
        "kind": kind,
        "title": title,
        "assignments": [],
        "courses": [],
        "action": None,
    }


def teacher_courses(teacher_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.course_code, c.course_name, c.semester,
                   COUNT(DISTINCT e.student_id) AS student_count,
                   COUNT(DISTINCT a.id) AS assignment_count
            FROM courses c
            LEFT JOIN enrollments e ON e.course_id = c.id
            LEFT JOIN assignments a ON a.course_id = c.id
            WHERE c.teacher_id = %s
            GROUP BY c.id, c.course_code, c.course_name, c.semester
            ORDER BY c.course_code
            """,
            (teacher_id,),
        ).fetchall()
    return [
        {
            "course_code": row[0],
            "course_name": row[1],
            "semester": row[2],
            "student_count": int(row[3] or 0),
            "assignment_count": int(row[4] or 0),
        }
        for row in rows
    ]


def teacher_assignments(teacher_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.course_code, c.course_name, a.title, a.due_date, a.max_marks,
                   COUNT(s.id) AS replies,
                   COUNT(s.id) FILTER (WHERE s.marks_obtained IS NULL) AS unchecked
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE c.teacher_id = %s
            GROUP BY c.course_code, c.course_name, a.id, a.title, a.due_date, a.max_marks
            ORDER BY a.due_date
            """,
            (teacher_id,),
        ).fetchall()
    items = []
    for row in rows:
        due = row[3]
        items.append(
            {
                "course_code": row[0],
                "course_name": row[1],
                "title": row[2],
                "due_date": due.strftime("%d %b %Y, %I:%M %p") if due else "",
                "max_marks": float(row[4] or 0),
                "replies": int(row[5] or 0),
                "unchecked": int(row[6] or 0),
            }
        )
    return items


def teacher_leave(teacher: dict[str, Any], teacher_id: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        _seed_teacher(conn, teacher_id, teacher.get("department") or "Computer Science")
        balances = _balance_rows(conn, teacher_id)
        conn.commit()
    return balances


def teacher_payslip(teacher: dict[str, Any]) -> dict[str, Any]:
    month = date.today().replace(day=1)
    slip = _salary_for(
        teacher.get("employee_id") or "",
        teacher.get("full_name") or "",
        teacher.get("department") or "",
        month,
    )
    return {
        "month_label": slip["month_label"],
        "gross": slip["gross"],
        "total_deduction": slip["total_deduction"],
        "net": slip["net"],
    }


def upcoming_holidays(limit: int = 4) -> list[dict[str, str]]:
    today = date.today()
    rows = [
        {"date": day.strftime("%d %b %Y"), "name": name}
        for day, name in HOLIDAYS_2026
        if day >= today
    ]
    return rows[:limit]


def _format_courses(courses: list[dict[str, Any]]) -> str:
    if not courses:
        return "You have no courses assigned."
    lines = [f"You teach **{len(courses)}** course(s):"]
    for course in courses:
        lines.append(
            f"- **{course['course_code']}** {course['course_name']} "
            f"(Sem {course['semester']}, {course['student_count']} students, "
            f"{course['assignment_count']} assignments)"
        )
    return "\n".join(lines)


def _format_assignments(items: list[dict[str, Any]], pending_only: bool) -> str:
    rows = [item for item in items if item["unchecked"] > 0] if pending_only else items
    if not rows:
        if pending_only:
            return "No submissions are waiting for a check."
        return "You have no assignments on your courses."
    title = "Submissions waiting for a check:" if pending_only else "Your assignments:"
    lines = [title]
    for item in rows:
        lines.append(
            f"- **{item['course_code']}** {item['title']} — due {item['due_date']}, "
            f"{item['replies']} replies, **{item['unchecked']} unchecked**"
        )
    lines.append("Open **LMS → Check Student Assignment** to enter marks.")
    return "\n".join(lines)


def _format_leave(balances: list[dict[str, Any]]) -> str:
    if not balances:
        return "No leave balances were found on your leave card."
    lines = ["Leave balance:"]
    for row in balances:
        lines.append(
            f"- **{row['leave_name']}**: {row['balance']:.2f} left "
            f"({row['taken']:.2f} taken of {row['total']:.2f})"
        )
    lines.append("Open **Establishment → Employee Leave Card** for the full card.")
    return "\n".join(lines)


GUIDE = {
    "question wise": "OBE → Question Wise Mark Entry. Pick the session and scheme, open the subject, then enter A/B/C marks.",
    "obe": "Use the **OBE** menu: Question Wise Mark Entry, Exam Question Paper, or Mark Entry Report.",
    "mark entry": "Examination → Mark Entry. Choose session, exam, course, and section, then Show.",
    "attendance register": "Academic → Attendance Register Report.",
    "attendance": "Academic → Attendance By Faculty.",
    "teaching plan": "Academic → Teaching Plan Entry.",
    "extra class": "Academic → Extra Class Time Table.",
    "payslip": "Payroll → Reports → Employee PaySlip.",
    "leave": "Establishment → Transactions → Employee Leave Card.",
    "assignment": "LMS → Select Course, then Create Assignment or Check Student Assignment.",
}


def _guide_for(normalized: str) -> str | None:
    if "question wise" in normalized or "obe" in normalized:
        return GUIDE["question wise"] if "question" in normalized or "mark" in normalized else GUIDE["obe"]
    for key in (
        "mark entry",
        "attendance register",
        "attendance",
        "teaching plan",
        "extra class",
        "payslip",
        "leave",
        "assignment",
    ):
        if key in normalized:
            return GUIDE[key]
    return None


def _is_creative_ask(normalized: str) -> bool:
    return _has_any(
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
            "what should i",
            "prioritize",
            "priority",
            "strategy",
            "summarize my",
            "help me decide",
            "workload",
            "day plan",
        ),
    )


def resolve_faculty_message(message: str, teacher: dict[str, Any], teacher_id: str) -> dict[str, Any] | None:
    normalized = message.casefold().strip()
    if not normalized:
        return None

    # Planning / advice prompts get an instant grounded plan from live data.
    if _is_creative_ask(normalized):
        from erp_ai.tools.academic_tools.quick_plans import build_faculty_focus_plan

        return _answer(
            build_faculty_focus_plan(
                teacher,
                teacher_courses(teacher_id),
                teacher_assignments(teacher_id),
            ),
            title="Focus plan",
            kind="help",
        )

    if normalized in {"hi", "hello", "hey", "help"} or _has_any(
        normalized, ("what can you do", "how can you help", "help me")
    ):
        return _answer(
            "I can look up **your courses**, **assignment checks**, **leave balance**, "
            "**payslip**, and where a faculty screen lives. "
            "Try: 'my courses', 'pending checks', 'leave balance', or 'my payslip'.",
            title="Faculty assistant",
        )

    asks_nav = _has_any(normalized, ("where", "how do i", "how to", "open"))
    guide = _guide_for(normalized) if asks_nav or _has_phrase(normalized, "where") else None
    if asks_nav and guide:
        return _answer(guide, title="Where to go")

    if _has_any(normalized, ("payslip", "salary", "net pay", "take home")):
        slip = teacher_payslip(teacher)
        return _answer(
            f"**{slip['month_label']}** payslip for {teacher.get('full_name')}: "
            f"gross ₹ {slip['gross']:,.2f}, deductions ₹ {slip['total_deduction']:,.2f}, "
            f"**net ₹ {slip['net']:,.2f}**.\n\n"
            "Open **Payroll → Employee PaySlip** for the full statement.",
            title="Payslip",
            kind="faculty_payslip",
        )

    if _has_any(normalized, ("holiday", "holidays")):
        rows = upcoming_holidays()
        if not rows:
            return _answer("No upcoming holidays are listed.", title="Holidays", kind="empty")
        lines = ["Upcoming holidays:"]
        lines.extend(f"- **{row['date']}** {row['name']}" for row in rows)
        return _answer("\n".join(lines), title="Holidays", kind="faculty_holidays")

    if _has_any(normalized, ("leave balance", "casual leave", "leave card", "my leave")) or (
        _has_phrase(normalized, "leave") and _has_any(normalized, ("balance", "show", "my", "how many"))
    ):
        return _answer(
            _format_leave(teacher_leave(teacher, teacher_id)),
            title="Leave balance",
            kind="faculty_leave",
        )

    if _has_any(
        normalized,
        ("pending check", "pending checks", "unchecked", "to grade", "submissions to check"),
    ) or (
        _has_phrase(normalized, "pending")
        and _has_any(normalized, ("assignment", "assignments", "submission", "submissions", "check"))
    ):
        return _answer(
            _format_assignments(teacher_assignments(teacher_id), pending_only=True),
            title="Pending checks",
            kind="faculty_assignments",
        )

    if _has_any(normalized, ("my assignment", "my assignments", "assignments i", "created assignment")):
        return _answer(
            _format_assignments(teacher_assignments(teacher_id), pending_only=False),
            title="Your assignments",
            kind="faculty_assignments",
        )

    if _has_any(
        normalized,
        ("my course", "my courses", "my subject", "my subjects", "what do i teach", "classes i teach"),
    ) or (
        _has_any(normalized, ("course", "courses", "subject", "subjects"))
        and _has_any(normalized, ("my", "teach", "show", "list"))
    ):
        return _answer(
            _format_courses(teacher_courses(teacher_id)),
            title="Your courses",
            kind="faculty_courses",
        )

    return None


def faculty_fallback() -> dict[str, Any]:
    return _answer(
        "I can help with your courses, assignment checks, leave balance, payslip, "
        "and where faculty screens are. Try 'my courses', 'pending checks', "
        "'leave balance', or 'my payslip'. "
        "The live model is temporarily unavailable; those lookups still work.",
        title="Faculty assistant",
    )
