"""Academic attendance read helpers (live faculty marks)."""

from __future__ import annotations

from typing import Any

from database import get_connection


def _roman(num: int) -> str:
    mapping = {
        1: "I",
        2: "II",
        3: "III",
        4: "IV",
        5: "V",
        6: "VI",
        7: "VII",
        8: "VIII",
    }
    return mapping.get(int(num), str(num))


def _ensure_faculty_attendance_tables() -> None:
    """Make sure faculty attendance tables exist before reading them."""
    try:
        from attendance_api import ensure_attendance_schema

        ensure_attendance_schema()
    except Exception:
        # Schema setup can fail if faculty seed is mid-flight; reader still
        # tries the query and returns empty totals when tables are missing.
        pass


def _percent(present: int, od: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(((present + od) / total) * 100, 2)


def build_attendance_summary(
    student_id: str,
    *,
    semester: int | None = None,
    session_label: str = "July-Dec 2026-2027",
) -> dict[str, Any]:
    """
    Aggregate live attendance from faculty_attendance_marks.

    Teacher submissions write into faculty_class_sessions +
    faculty_attendance_marks. This reader is the student-side mirror of that
    data, so dashboard / profile / chatbot stay in sync after a teacher update.
    """
    _ensure_faculty_attendance_tables()

    with get_connection() as conn:
        if semester is None:
            row = conn.execute(
                "SELECT semester FROM students WHERE id = %s",
                (student_id,),
            ).fetchone()
            semester = int(row[0]) if row else 0

        rows = conn.execute(
            """
            SELECT
                c.course_code,
                c.course_name,
                COALESCE(
                    SUM(CASE WHEN m.mark = 'P' THEN 1 ELSE 0 END),
                    0
                ) AS present_count,
                COALESCE(
                    SUM(CASE WHEN m.mark = 'A' THEN 1 ELSE 0 END),
                    0
                ) AS absent_count,
                COALESCE(
                    SUM(CASE WHEN m.mark = 'OD' THEN 1 ELSE 0 END),
                    0
                ) AS od_count,
                COUNT(m.id) AS total_count
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            LEFT JOIN faculty_class_sessions s
                ON s.course_id = c.id
            LEFT JOIN faculty_attendance_marks m
                ON m.session_id = s.id
               AND m.student_id = e.student_id
            WHERE e.student_id = %s
            GROUP BY c.course_code, c.course_name
            ORDER BY c.course_code
            """,
            (student_id,),
        ).fetchall()

    subjects: list[dict[str, Any]] = []
    total_classes = 0
    present = 0
    absent = 0
    od = 0

    for code, name, p_count, a_count, od_count, total in rows:
        p_count = int(p_count or 0)
        a_count = int(a_count or 0)
        od_count = int(od_count or 0)
        total = int(total or 0)
        subjects.append(
            {
                "code": code,
                "name": name,
                "type": "Theory",
                "total": total,
                "present": p_count,
                "absent": a_count,
                "od": od_count,
                "percent": _percent(p_count, od_count, total),
                "lectures": f"{p_count + od_count}/{total}" if total else "0/0",
            }
        )
        total_classes += total
        present += p_count
        absent += a_count
        od += od_count

    return {
        "session": f"{session_label} ({_roman(int(semester or 0))})",
        "total_classes": total_classes,
        "present": present,
        "absent": absent,
        "od": od,
        "percent": _percent(present, od, total_classes),
        "subjects": subjects,
    }


def get_attendance_summary(student_id: str) -> dict[str, Any]:
    """Return attendance summary for the student (read-only, live marks)."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT full_name, semester, branch
            FROM students
            WHERE id = %s
            """,
            (student_id,),
        ).fetchone()

    if not row:
        raise ValueError("Student profile not found")

    full_name, semester, branch = row
    summary = build_attendance_summary(
        student_id,
        semester=int(semester),
    )
    return {
        "full_name": full_name,
        "semester": semester,
        "branch": branch,
        **summary,
    }


def format_attendance_summary(summary: dict[str, Any]) -> str:
    """Markdown summary for chatbot answers."""
    lines = [
        "### Attendance summary\n",
        f"- **Student:** {summary.get('full_name')}",
        f"- **Session:** {summary.get('session')}",
        f"- **Total classes:** {summary.get('total_classes')}",
        f"- **Present:** {summary.get('present')}",
        f"- **Absent:** {summary.get('absent')}",
        f"- **OD:** {summary.get('od')}",
        f"- **Overall %:** {summary.get('percent')}",
        "",
        "#### Subject-wise",
    ]
    subjects = summary.get("subjects") or []
    if not subjects:
        lines.append("- No subject attendance rows available.")
    else:
        for item in subjects:
            lines.append(
                f"- **{item.get('code')}** {item.get('name')}: "
                f"{item.get('present')}/{item.get('total')} "
                f"({item.get('percent')}%)"
            )
    return "\n".join(lines) + "\n"
