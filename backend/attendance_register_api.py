"""Faculty Attendance Register Report.

Filters a teacher's marked lectures and returns the summary plus the
student-wise register.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

from attendance_api import ensure_attendance_schema
from faculty_api import _require_teacher

router = APIRouter(prefix="/api/faculty/attendance-register", tags=["attendance-register"])

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
SESSIONS = (
    ("July-Dec 2026-2027", date(2026, 7, 1), date(2026, 12, 31)),
    ("July-Dec 2025-2026", date(2025, 7, 1), date(2025, 12, 31)),
)
COURSE_TYPES = ("Theory", "Practical", "Theory and Practical", "Tutorial")
CLASS_TYPES = {
    "Theory": ("Regular Class", "Extra Class"),
    "Practical": ("Practical",),
    "Theory and Practical": ("Regular Class", "Extra Class", "Practical"),
    "Tutorial": ("Tutorial",),
}


class RegisterIn(BaseModel):
    tab: str = "courses"
    session_label: str
    course_id: str
    college_label: str = ""
    semester: int | None = None
    course_type: str = ""
    section: str = ""
    from_date: str
    to_date: str


def _colleges_for(department: str) -> list[str]:
    dept = department or "Computer Science"
    return [
        f"SET - B. Tech-{dept}- 2023-24- Department of {dept} (New)",
        f"SET - Bachelor of Technology-{dept}- 2023-24- Department of {dept} (New)",
    ]


def _fmt(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _parse_date(value: str) -> date:
    raw = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Enter a valid date")


def _session_window(label: str) -> tuple[date, date]:
    for name, start, end in SESSIONS:
        if name == label:
            return start, end
    raise HTTPException(status_code=400, detail="Select a session")


def _percentage(present: int, od: int, total: int) -> float:
    if not total:
        return 0.0
    return round((present + od) * 100 / total, 2)


@router.get("/options")
def register_options(access_token: str | None = Cookie(default=None)):
    ensure_attendance_schema()
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        courses = conn.execute(
            """
            SELECT id, course_code, course_name, semester
            FROM courses
            WHERE teacher_id = %s
            ORDER BY semester, course_code
            """,
            (teacher_id,),
        ).fetchall()
        sections = conn.execute(
            """
            SELECT DISTINCT section
            FROM faculty_class_sessions
            WHERE teacher_id = %s
            ORDER BY section
            """,
            (teacher_id,),
        ).fetchall()

    course_rows = [
        {
            "id": str(course_id),
            "course_code": code,
            "course_name": name,
            "label": f"{code} - {name}",
            "semester": int(semester),
            "semester_label": ROMAN.get(int(semester), str(semester)),
        }
        for course_id, code, name, semester in courses
    ]
    return {
        "sessions": [name for name, _start, _end in SESSIONS],
        "colleges": _colleges_for(teacher["department"]),
        "courses": course_rows,
        "elective_courses": [],
        "course_types": list(COURSE_TYPES),
        "sections": [row[0] for row in sections] or ["S"],
        "faculty_name": teacher["full_name"],
        "employee_id": teacher["employee_id"],
    }


def _build_register(teacher: dict[str, Any], teacher_id: str, body: RegisterIn) -> dict[str, Any]:
    if body.tab not in ("courses", "elective"):
        raise HTTPException(status_code=400, detail="Select a course tab")
    if body.tab == "elective":
        raise HTTPException(
            status_code=400,
            detail="No free or global elective course is assigned to you",
        )
    window_start, window_end = _session_window(body.session_label)
    start = _parse_date(body.from_date)
    end = _parse_date(body.to_date)
    if end < start:
        raise HTTPException(status_code=400, detail="To Date cannot be before From Date")
    if body.course_type and body.course_type not in COURSE_TYPES:
        raise HTTPException(status_code=400, detail="Select a course type")
    if body.college_label and body.college_label not in _colleges_for(teacher["department"]):
        raise HTTPException(status_code=400, detail="Select a college")

    range_start = max(start, window_start)
    range_end = min(end, window_end)
    class_types = CLASS_TYPES.get(body.course_type) if body.course_type else None

    with get_connection() as conn:
        course = conn.execute(
            """
            SELECT id, course_code, course_name, semester
            FROM courses
            WHERE id = %s AND teacher_id = %s
            """,
            (body.course_id, teacher_id),
        ).fetchone()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found for this teacher")
        if body.semester is not None and int(body.semester) != int(course[3]):
            raise HTTPException(status_code=400, detail="Semester does not match the selected course")

        filters = [
            "s.teacher_id = %s",
            "s.course_id = %s",
            "s.attendance_status = 'Completed'",
            "s.session_date BETWEEN %s AND %s",
        ]
        params: list[Any] = [teacher_id, body.course_id, range_start, range_end]
        if range_start > range_end:
            rows = []
        else:
            if body.section:
                filters.append("s.section = %s")
                params.append(body.section)
            if class_types:
                filters.append("s.class_type = ANY(%s)")
                params.append(list(class_types))
            rows = conn.execute(
                f"""
                SELECT s.id, s.session_date, s.slot_label, s.slot_sort, s.section,
                       s.class_type, st.roll_number, st.full_name, m.mark
                FROM faculty_class_sessions s
                JOIN faculty_attendance_marks m ON m.session_id = s.id
                JOIN students st ON st.id = m.student_id
                WHERE {' AND '.join(filters)}
                ORDER BY s.session_date, s.slot_sort, st.roll_number
                """,
                params,
            ).fetchall()

    sessions: dict[str, dict[str, Any]] = {}
    students: dict[str, dict[str, Any]] = {}
    for session_id, session_date, slot_label, _slot_sort, section, class_type, roll, name, mark in rows:
        key = str(session_id)
        if key not in sessions:
            sessions[key] = {
                "id": key,
                "date": _fmt(session_date),
                "label": session_date.strftime("%d/%m"),
                "slot": slot_label,
                "section": section,
                "class_type": class_type,
                "present": 0,
                "absent": 0,
                "od": 0,
                "total": 0,
            }
        bucket = sessions[key]
        bucket["total"] += 1
        if mark == "A":
            bucket["absent"] += 1
        elif mark == "OD":
            bucket["od"] += 1
        else:
            bucket["present"] += 1

        student = students.get(roll)
        if student is None:
            student = {
                "roll_number": roll,
                "full_name": name,
                "present": 0,
                "absent": 0,
                "od": 0,
                "total": 0,
                "marks": {},
            }
            students[roll] = student
        student["total"] += 1
        student["marks"][key] = mark
        if mark == "A":
            student["absent"] += 1
        elif mark == "OD":
            student["od"] += 1
        else:
            student["present"] += 1

    session_rows = []
    for item in sessions.values():
        item["percentage"] = _percentage(item["present"], item["od"], item["total"])
        session_rows.append(item)

    student_rows = []
    for item in students.values():
        ordered = [item["marks"].get(session["id"], "") for session in session_rows]
        student_rows.append(
            {
                "roll_number": item["roll_number"],
                "full_name": item["full_name"],
                "present": item["present"],
                "absent": item["absent"],
                "od": item["od"],
                "total": item["total"],
                "percentage": _percentage(item["present"], item["od"], item["total"]),
                "marks": ordered,
            }
        )

    semester = int(course[3])
    return {
        "header": {
            "session_label": body.session_label,
            "college_label": body.college_label,
            "course_label": f"{course[1]} - {course[2]}",
            "semester_label": ROMAN.get(semester, str(semester)),
            "section": body.section or "All",
            "course_type": body.course_type or "All",
            "from_date": _fmt(start),
            "to_date": _fmt(end),
            "faculty_name": teacher["full_name"],
            "employee_id": teacher["employee_id"],
        },
        "sessions": session_rows,
        "students": student_rows,
    }


@router.post("")
def attendance_register(
    body: RegisterIn,
    access_token: str | None = Cookie(default=None),
):
    ensure_attendance_schema()
    teacher, teacher_id = _require_teacher(access_token)
    if not body.course_id:
        raise HTTPException(status_code=400, detail="Select a course")
    if not body.from_date or not body.to_date:
        raise HTTPException(status_code=400, detail="Select the from and to dates")
    return _build_register(teacher, teacher_id, body)
