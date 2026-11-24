"""Examination admit-card read helpers (shared by HTTP API + chatbot)."""

from __future__ import annotations

from typing import Any

from database import get_connection

from erp_ai.tools.examination_tools.timetable import (
    DEFAULT_EXAM_NAME,
    DEFAULT_SESSION,
)


def _student_and_courses(
    student_id: str,
) -> tuple[tuple[Any, ...], list[tuple[Any, ...]]]:
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

        courses = conn.execute(
            """
            SELECT c.course_code, c.course_name, c.semester
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            WHERE e.student_id = %s
            ORDER BY c.course_code
            """,
            (student_id,),
        ).fetchall()
    return row, courses


def get_admit_card(student_id: str) -> dict[str, Any]:
    """Return admit card payload for the student (read-only)."""
    student, courses = _student_and_courses(student_id)
    _id, roll_number, full_name, semester, branch, email = student
    degree = (
        "Bachelor of Technology - Computer Science and Engineering"
        if branch == "CSE"
        else f"Bachelor of Technology - {branch}"
    )

    return {
        "admit_card": {
            "student_name": full_name,
            "rrn": roll_number,
            "email": email,
            "semester": semester,
            "branch": degree,
            "admission_batch": "2025-2029",
            "scheme": f"B.Tech-{branch}-2025-2029-Department of Computer Engineering (New)",
            "exam_name": DEFAULT_EXAM_NAME,
            "session": DEFAULT_SESSION,
            "center": "JECRC University Main Campus",
            "instructions": [
                "Carry this admit card and a valid photo ID to the exam hall.",
                "Report at least 30 minutes before the exam start time.",
                "Electronic devices are not allowed inside the examination hall.",
            ],
            "papers": [
                {
                    "code": c[0],
                    "name": c[1],
                    "date": f"{12 + (i % 10):02d}-Dec-2026",
                    "slot": (
                        "09:00 AM - 12:00 PM"
                        if i % 2 == 0
                        else "01:00 PM - 04:00 PM"
                    ),
                }
                for i, c in enumerate(courses)
            ],
        }
    }

