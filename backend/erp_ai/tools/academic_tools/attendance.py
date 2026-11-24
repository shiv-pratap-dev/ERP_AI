"""Academic attendance read helpers (live faculty marks)."""

from __future__ import annotations

from typing import Any

from database import get_connection


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
    session_label: str = "Jul 2026 - Dec 2026 (3rd Semester)",
) -> dict[str, Any]:
    """
    Aggregate live attendance from faculty_attendance_marks.

    Teacher submissions write into faculty_class_sessions +
    faculty_attendance_marks. This reader is the student-side mirror of that
    data, so dashboard / profile / chatbot stay in sync after a teacher update.
    """
    _ensure_faculty_attendance_tables()
    _ = semester

    with get_connection() as conn:
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
        "session": session_label,
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


from erp_ai.chat.normalizer import (
    COURSE_ALIASES,
    expand_course_aliases,
    has_phrase,
    match_course_from_list,
    normalize_alnum,
    normalize_chat_text,
)


def filter_attendance_subjects(
    summary: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    """Keep only subjects that match a course code/name/alias in the query."""
    normalized = normalize_alnum(normalize_chat_text(query))
    if not normalized:
        return summary

    subjects = list(summary.get("subjects") or [])
    matched: list[dict[str, Any]] = []

    best_match, candidates = match_course_from_list(
        query,
        subjects,
        code_key="code",
        name_key="name",
    )
    if best_match is not None:
        matched = [best_match]
    else:
        search_terms = expand_course_aliases(query)
        for item in subjects:
            code = normalize_alnum(str(item.get("code") or ""))
            name = normalize_alnum(str(item.get("name") or ""))
            haystack = f"{code} {name}".strip()
            if code and has_phrase(normalized, code):
                matched.append(item)
            elif any(term and term in haystack for term in search_terms[1:]):
                matched.append(item)
            elif name and name in normalized:
                matched.append(item)
        if not matched and candidates:
            matched = candidates

    # No subject hint in the query → keep full summary.
    if not matched:
        words = set(normalized.split())
        hinted = any(
            alias in words or has_phrase(normalized, alias)
            for alias in COURSE_ALIASES
        ) or any(
            token in words or token in normalized
            for token in (
                "bco",
                "bas",
                "den",
                "din",
                "dma",
                "lab",
                "networks",
                "operating",
                "software",
                "discrete",
            )
        )
        if not hinted:
            return summary
        return {
            **summary,
            "subjects": [],
            "total_classes": 0,
            "present": 0,
            "absent": 0,
            "od": 0,
            "percent": 0.0,
            "filter_query": query,
            "filter_matched": False,
        }

    total_classes = sum(int(item.get("total") or 0) for item in matched)
    present = sum(int(item.get("present") or 0) for item in matched)
    absent = sum(int(item.get("absent") or 0) for item in matched)
    od = sum(int(item.get("od") or 0) for item in matched)
    return {
        **summary,
        "subjects": matched,
        "total_classes": total_classes,
        "present": present,
        "absent": absent,
        "od": od,
        "percent": _percent(present, od, total_classes),
        "filter_query": query,
        "filter_matched": True,
    }

