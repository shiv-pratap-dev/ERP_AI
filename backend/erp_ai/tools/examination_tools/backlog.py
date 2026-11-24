"""Examination backlog read helpers (shared by HTTP API + chatbot)."""

from __future__ import annotations

from typing import Any

from database import get_connection

from erp_ai.tools.examination_tools.timetable import DEFAULT_SESSION

# Demo backlog papers for older semesters (read-only).
_DEMO_BACKLOG = [
    {
        "code": "DMA002A",
        "name": "Engineering Mathematics-II",
        "credit": 4.0,
        "semester": 2,
        "course_type": "Theory",
        "grade": "F",
        "exam_fees": 1400.0,
    },
    {
        "code": "BCO101A",
        "name": "Programming Fundamentals",
        "credit": 3.0,
        "semester": 1,
        "course_type": "Theory",
        "grade": "F",
        "exam_fees": 1400.0,
    },
]


def _student_row(student_id: str) -> tuple[Any, ...]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.roll_number, s.full_name, s.semester, s.branch, u.email
            FROM students s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = %s
            """,
            (student_id,),
        ).fetchone()
    if not row:
        raise ValueError("Student profile not found")
    return row


def get_backlog_courses(
    student_id: str,
    semester: int | None = None,
) -> dict[str, Any]:
    """Return backlog course list and fee summary (read-only)."""
    student = _student_row(student_id)
    _id, roll_number, full_name, current_sem, branch, email = student
    selected = semester if semester is not None else 2

    rows = [row for row in _DEMO_BACKLOG if row["semester"] == selected]
    subjects_fee = sum(r["exam_fees"] for r in rows)

    return {
        "student": {
            "name": full_name,
            "rrn": roll_number,
            "email": email,
            "semester": current_sem,
            "branch": (
                "Bachelor of Technology - Computer Science and Engineering"
                if branch == "CSE"
                else f"Bachelor of Technology - {branch}"
            ),
            "scheme": f"B.Tech-{branch}-2025-2029-Department of Computer Engineering (New)",
            "admission_batch": "2025-2029",
            "session": DEFAULT_SESSION,
        },
        "selected_semester": selected,
        "fee_summary": {
            "processing_fee": 0,
            "certificate_fee": 0,
            "subjects_fee": subjects_fee,
            "late_fee": 0,
            "total_fee": subjects_fee,
            "paid_fee": 0,
        },
        "courses": rows,
        "warning": (
            "This Is One Time Activity, Kindly Verify Data Before "
            "Final Registration."
        ),
    }

