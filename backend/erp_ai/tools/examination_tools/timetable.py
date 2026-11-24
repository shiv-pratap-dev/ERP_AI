"""Examination timetable read helpers (shared by HTTP API + chatbot)."""

from __future__ import annotations

from typing import Any

from database import get_connection

DEFAULT_SESSION = "Jul 2026 - Dec 2026 (3rd Semester)"
DEFAULT_EXAM_NAME = "END TERM"

EXAM_SESSIONS = [
    "Jul 2025 - Dec 2025 (1st Semester)",
    "Jan 2026 - Jun 2026 (2nd Semester)",
    "Jul 2026 - Dec 2026 (3rd Semester)",
]

EXAM_NAMES = [
    "INTERNAL EXAM",
    "ASSIGNMENT",
    "INTERNAL PRACTICAL",
    "END TERM",
]


def get_exam_meta() -> dict[str, Any]:
    """Return available exam sessions and names (read-only)."""
    return {
        "sessions": list(EXAM_SESSIONS),
        "exam_names": list(EXAM_NAMES),
    }


def _enrolled_courses(student_id: str) -> list[tuple[Any, ...]]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT c.course_code, c.course_name, c.semester
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            WHERE e.student_id = %s
            ORDER BY c.course_code
            """,
            (student_id,),
        ).fetchall()


def get_exam_timetable(
    student_id: str,
    session: str | None = None,
    exam_name: str | None = None,
) -> dict[str, Any]:
    """Return exam timetable rows for the student (read-only demo data)."""
    session_label = (session or DEFAULT_SESSION).strip()
    exam_label = (exam_name or DEFAULT_EXAM_NAME).strip()

    if not session_label or not exam_label:
        raise ValueError("Session and Exam Name are required")

    courses = _enrolled_courses(student_id)

    # Demo timetable for END TERM / INTERNAL; others intentionally empty.
    if exam_label.upper() not in {"END TERM", "INTERNAL EXAM"}:
        return {
            "session": session_label,
            "exam_name": exam_label,
            "rows": [],
        }

    slots = ["09:00 AM - 12:00 PM", "01:00 PM - 04:00 PM"]
    rows: list[dict[str, Any]] = []
    for index, course in enumerate(courses[:6]):
        day = 10 + index
        rows.append(
            {
                "exam_date": f"{day:02d}-Dec-2026",
                "slot": slots[index % 2],
                "code": course[0],
                "course": course[1],
                "semester": course[2],
                "type": "Regular",
            }
        )

    return {
        "session": session_label,
        "exam_name": exam_label,
        "rows": rows,
    }

