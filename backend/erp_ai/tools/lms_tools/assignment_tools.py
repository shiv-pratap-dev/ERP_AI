"""Assignment-domain tools used by the assistant and future MCP server."""

from datetime import datetime
import re
from typing import Any

from database import get_connection
from erp_ai.chat.normalizer import (
    expand_course_aliases,
    extract_assignment_number,
    has_phrase,
    match_course_from_list,
    normalize_alnum,
    normalize_chat_text,
)


def list_current_assignments(student_id: str, semester: int) -> list[dict[str, Any]]:
    """Return assignments visible to a student for a semester."""
    from faculty_api import ensure_lms_schema

    with get_connection() as conn:
        ensure_lms_schema(conn)
        rows = conn.execute(
            """
            SELECT a.id, c.course_code, c.course_name, a.title,
                   a.due_date, s.status,
                   CASE
                       WHEN COALESCE(s.display_to_student, TRUE) THEN s.marks_obtained
                       ELSE NULL
                   END AS marks_obtained,
                   a.max_marks,
                   s.submitted_at
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id AND c.semester = %s
            JOIN assignments a ON a.course_id = c.id
            LEFT JOIN submissions s
              ON s.assignment_id = a.id AND s.student_id = e.student_id
            WHERE e.student_id = %s
              AND COALESCE(c.subject_type, 'Theory') <> 'Practical'
              AND (
                NOT EXISTS (
                    SELECT 1 FROM assignment_recipients r
                    WHERE r.assignment_id = a.id
                )
                OR EXISTS (
                    SELECT 1 FROM assignment_recipients r
                    WHERE r.assignment_id = a.id
                      AND r.student_id = e.student_id
                )
              )
            ORDER BY a.due_date ASC, c.course_code ASC
            """,
            (semester, student_id),
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "course_code": row[1],
            "course_name": row[2],
            "title": row[3],
            "due_date": row[4].isoformat(),
            "status": row[5] or "pending",
            "marks_obtained": float(row[6]) if row[6] is not None else None,
            "max_marks": float(row[7]) if row[7] is not None else None,
            "submitted_at": row[8].isoformat() if row[8] is not None else None,
        }
        for row in rows
    ]


def find_assignment_matches(
    message: str,
    assignments: list[dict[str, Any]],
    *,
    assignment_number: int | None = None,
) -> list[dict[str, Any]]:
    """Return all assignments matching course alias, course code, or title."""
    normalized = normalize_alnum(normalize_chat_text(message))
    if not normalized and assignment_number is None:
        return []

    search_terms = expand_course_aliases(message)
    requested_number = (
        assignment_number
        if assignment_number is not None
        else extract_assignment_number(message)
    )

    best_course, _candidates = match_course_from_list(
        message,
        assignments,
        code_key="course_code",
        name_key="course_name",
    )
    best_course_code = (
        str(best_course.get("course_code") or "") if best_course else ""
    )

    matches: dict[str, dict[str, Any]] = {}

    for assignment in assignments:
        title = str(assignment.get("title", ""))
        course_code = str(assignment.get("course_code", ""))
        course_name = str(assignment.get("course_name", ""))

        title_normalized = normalize_alnum(title)
        code_normalized = normalize_alnum(course_code)
        course_normalized = normalize_alnum(course_name)

        # Require the requested number to match the assignment title.
        if requested_number is not None:
            title_number = re.search(
                r"\b(?:assignment|assign|hw|homework|task)?\s*([0-9]+)\b",
                title_normalized,
            )
            exact_title_num = re.search(
                r"\bassignment\s*([0-9]+)\b",
                title_normalized,
            )
            num_str = (
                exact_title_num.group(1)
                if exact_title_num
                else (title_number.group(1) if title_number else None)
            )
            if not num_str or int(num_str) != int(requested_number):
                continue

        # Match a course code or a full course name/alias.
        course_matched = bool(
            (best_course_code and course_code == best_course_code)
            or (code_normalized and has_phrase(normalized, code_normalized))
        )

        if not course_matched:
            course_matched = any(
                term in course_normalized or term in title_normalized
                for term in search_terms[1:]
            )

        if not course_matched and course_normalized:
            course_matched = course_normalized in normalized

        # Also support an explicitly mentioned full assignment title.
        title_matched = bool(title_normalized and title_normalized in normalized)

        # If only assignment_number was passed with an empty/generic course hint
        if not normalized and requested_number is not None:
            matches[str(assignment["id"])] = assignment
            continue

        if course_matched or title_matched:
            matches[str(assignment["id"])] = assignment

    return list(matches.values())



def find_assignment(
    message: str,
    assignments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match a single assignment by course alias, course code, or title."""
    matches = find_assignment_matches(message, assignments)

    # Never guess when multiple assignments remain.
    if len(matches) == 1:
        return matches[0]

    return None


def assignment_date(assignment: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(assignment["due_date"])


def list_my_courses(student_id: str, semester: int) -> list[dict[str, Any]]:
    """Return courses enrolled by the authenticated student for a semester."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                c.id,
                c.course_code,
                c.course_name,
                c.semester,
                c.description,
                t.full_name AS teacher_name
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            LEFT JOIN teachers t ON t.id = c.teacher_id
            WHERE e.student_id = %s
              AND c.semester = %s
            ORDER BY c.course_code
            """,
            (student_id, semester),
        ).fetchall()

    return [
        {
            "id": str(row[0]),
            "course_code": row[1],
            "course_name": row[2],
            "semester": row[3],
            "description": row[4],
            "teacher": row[5],
        }
        for row in rows
    ]
