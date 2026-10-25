"""Assignment-domain tools used by the assistant and future MCP server."""

from datetime import datetime
from typing import Any
import re
from typing import Any

from database import get_connection


def list_current_assignments(student_id: str, semester: int) -> list[dict[str, Any]]:
    """Return assignments visible to a student for a semester."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id, c.course_code, c.course_name, a.title,
                   a.due_date, s.status
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id AND c.semester = %s
            JOIN assignments a ON a.course_id = c.id
            LEFT JOIN submissions s
              ON s.assignment_id = a.id AND s.student_id = e.student_id
            WHERE e.student_id = %s
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
        }
        for row in rows
    ]




def find_assignment(
    message: str,
    assignments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Match assignments by course alias, course code, or title."""

    normalized = re.sub(
        r"[^a-z0-9]+", " ", message.casefold()
    ).strip()
    words = set(normalized.split())

    aliases = {
        "dbms": ("database management systems",),
        "dsa": ("data structures and algorithms",),
        "os": ("operating systems",),
        "cn": ("computer networks",),
    }

    # Expand aliases into their full course names.
    search_terms = [normalized]

    for alias, expansions in aliases.items():
        if alias in words:
            search_terms.extend(expansions)

    # Identify an explicitly requested assignment number.
    number_match = re.search(
        r"\b(?:assignment|assign|hw|homework)\s*(?:number\s*)?([0-9]+)\b",
        normalized,
    )
    requested_number = number_match.group(1) if number_match else None

    matches = {}

    for assignment in assignments:
        title = str(assignment.get("title", ""))
        course_code = str(assignment.get("course_code", ""))
        course_name = str(assignment.get("course_name", ""))

        title_normalized = re.sub(
            r"[^a-z0-9]+", " ", title.casefold()
        ).strip()
        code_normalized = re.sub(
            r"[^a-z0-9]+", " ", course_code.casefold()
        ).strip()
        course_normalized = re.sub(
            r"[^a-z0-9]+", " ", course_name.casefold()
        ).strip()

        # Require the requested number to match the assignment title.
        if requested_number:
            title_number = re.search(
                r"\bassignment\s*([0-9]+)\b",
                title_normalized,
            )
            if not title_number or title_number.group(1) != requested_number:
                continue

        # Match a course code or a full course name/alias.
        course_matched = bool(
            code_normalized
            and re.search(
                rf"\b{re.escape(code_normalized)}\b",
                normalized,
            )
        )

        if not course_matched:
            course_matched = any(
                term in course_normalized or term in title_normalized
                for term in search_terms[1:]
            )

        # Also support an explicitly mentioned full assignment title.
        title_matched = (
            bool(title_normalized)
            and title_normalized in normalized
        )

        if course_matched or title_matched:
            matches[str(assignment["id"])] = assignment

    # Never guess when multiple assignments remain.
    if len(matches) == 1:
        return next(iter(matches.values()))

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
