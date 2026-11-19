"""Faculty Examination → Mark Entry."""

from __future__ import annotations

from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException, Query
from pydantic import BaseModel, Field

from faculty_api import _assert_course_owned, _require_teacher

router = APIRouter(prefix="/api/faculty/mark-entry", tags=["mark-entry"])

SESSIONS = ("July-Dec 2026-2027", "July-Dec 2025-2026")
SECTIONS = ("S", "A", "B", "C")
ENROLLED_SECTION = "S"
STATUS_CODES = ("902", "903", "904")
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
EXAMS: dict[str, float] = {
    "II-IN TERM": 40.0,
    "INTERNAL EXAM": 30.0,
    "ASSIGNMENT": 10.0,
    "INTERNAL PRACTICAL": 20.0,
    "END TERM": 70.0,
}
SEEDED_EXAM = "II-IN TERM"
LOCK_MESSAGE = "You won't be able to modify the MARKS after it's been locked"

_schema_ready = False


class MarkRowIn(BaseModel):
    student_id: str
    marks: float | None = None
    status_code: str | None = None


class SaveMarksIn(BaseModel):
    session_label: str
    exam_name: str
    course_id: str
    section: str
    rows: list[MarkRowIn] = Field(default_factory=list)


def _colleges_for(department: str) -> list[str]:
    dept = department or "Computer Science"
    return [
        f"SET - B. Tech-{dept}- 2023-24- Department of {dept} (New)",
        f"SET - Bachelor of Technology-{dept}- 2023-24- Department of {dept} (New)",
    ]


def ensure_mark_entry_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faculty_exam_sheets (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                session_label VARCHAR(40) NOT NULL,
                exam_name VARCHAR(40) NOT NULL,
                section VARCHAR(10) NOT NULL,
                locked BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (teacher_id, course_id, session_label, exam_name, section)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faculty_exam_marks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                sheet_id UUID NOT NULL REFERENCES faculty_exam_sheets(id) ON DELETE CASCADE,
                student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                marks_obtained NUMERIC(6,2),
                status_code VARCHAR(10),
                UNIQUE (sheet_id, student_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_faculty_exam_sheets_teacher
                ON faculty_exam_sheets(teacher_id, course_id, exam_name)
            """
        )
        conn.commit()
    _schema_ready = True


def _seed_sheet(conn, teacher_id: str, course_id: str, session_label: str) -> None:
    """Fill II-IN TERM once so the first Show matches a sheet that already has marks."""
    existing = conn.execute(
        """
        SELECT 1 FROM faculty_exam_sheets
        WHERE teacher_id = %s AND course_id = %s
          AND session_label = %s AND exam_name = %s AND section = %s
        """,
        (teacher_id, course_id, session_label, SEEDED_EXAM, ENROLLED_SECTION),
    ).fetchone()
    if existing:
        return

    sheet = conn.execute(
        """
        INSERT INTO faculty_exam_sheets
            (teacher_id, course_id, session_label, exam_name, section)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (teacher_id, course_id, session_label, SEEDED_EXAM, ENROLLED_SECTION),
    ).fetchone()
    students = conn.execute(
        """
        SELECT s.id
        FROM enrollments e
        JOIN students s ON s.id = e.student_id
        WHERE e.course_id = %s
        ORDER BY s.roll_number
        """,
        (course_id,),
    ).fetchall()
    max_marks = EXAMS[SEEDED_EXAM]
    for index, student in enumerate(students):
        status = None
        marks = None
        if index % 8 == 3:
            status = "902"
        elif index % 11 == 7:
            status = "903"
        else:
            marks = round(max_marks * (0.45 + (index % 5) * 0.1), 2)
        conn.execute(
            """
            INSERT INTO faculty_exam_marks (sheet_id, student_id, marks_obtained, status_code)
            VALUES (%s, %s, %s, %s)
            """,
            (sheet[0], student[0], marks, status),
        )


def _course_type(course_name: str) -> str:
    name = (course_name or "").lower()
    if "lab" in name or "practical" in name:
        return "Practical"
    return "Theory"


def _students_for(conn, course_id: str, section: str) -> list[tuple]:
    if section != ENROLLED_SECTION:
        return []
    return conn.execute(
        """
        SELECT s.id::text, s.roll_number, s.full_name
        FROM enrollments e
        JOIN students s ON s.id = e.student_id
        WHERE e.course_id = %s
        ORDER BY s.roll_number
        """,
        (course_id,),
    ).fetchall()


def _sheet_row(conn, teacher_id: str, course_id: str, session_label: str, exam_name: str, section: str):
    return conn.execute(
        """
        SELECT id, locked
        FROM faculty_exam_sheets
        WHERE teacher_id = %s AND course_id = %s
          AND session_label = %s AND exam_name = %s AND section = %s
        """,
        (teacher_id, course_id, session_label, exam_name, section),
    ).fetchone()


def _validate_filters(session_label: str, exam_name: str, section: str) -> None:
    if session_label not in SESSIONS:
        raise HTTPException(status_code=400, detail="Select a session")
    if exam_name not in EXAMS:
        raise HTTPException(status_code=400, detail="Select an exam name")
    if section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Select a section")


@router.get("/meta")
def mark_entry_meta(access_token: str | None = Cookie(default=None)) -> dict[str, Any]:
    ensure_mark_entry_schema()
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        courses = conn.execute(
            """
            SELECT id::text, course_code, course_name, semester
            FROM courses
            WHERE teacher_id = %s
            ORDER BY course_code
            """,
            (teacher_id,),
        ).fetchall()
        for course in courses:
            _seed_sheet(conn, teacher_id, course[0], SESSIONS[0])
        conn.commit()

    return {
        "sessions": list(SESSIONS),
        "exams": [{"name": name, "max_marks": marks} for name, marks in EXAMS.items()],
        "sections": list(SECTIONS),
        "colleges": _colleges_for(teacher["department"]),
        "courses": [
            {
                "id": row[0],
                "code": row[1],
                "name": row[2],
                "semester": row[3],
                "semester_label": ROMAN.get(row[3], str(row[3])),
                "course_type": _course_type(row[2]),
                "section": ENROLLED_SECTION,
            }
            for row in courses
        ],
        "status_codes": [
            {"code": "902", "label": "Absent"},
            {"code": "903", "label": "UFM"},
            {"code": "904", "label": "Detained"},
        ],
        "lock_message": LOCK_MESSAGE,
    }


@router.get("/sheet")
def mark_entry_sheet(
    session_label: str = Query(...),
    exam_name: str = Query(...),
    course_id: str = Query(...),
    section: str = Query(...),
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    ensure_mark_entry_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    _validate_filters(session_label, exam_name, section)

    with get_connection() as conn:
        course = _assert_course_owned(conn, course_id, teacher_id)
        students = _students_for(conn, course_id, section)
        sheet = _sheet_row(conn, teacher_id, course_id, session_label, exam_name, section)
        saved: dict[str, tuple] = {}
        locked = False
        if sheet:
            locked = bool(sheet[1])
            rows = conn.execute(
                """
                SELECT student_id::text, marks_obtained, status_code
                FROM faculty_exam_marks
                WHERE sheet_id = %s
                """,
                (sheet[0],),
            ).fetchall()
            saved = {row[0]: row for row in rows}

    max_marks = EXAMS[exam_name]
    payload_rows = []
    for student in students:
        stored = saved.get(student[0])
        marks = None if not stored or stored[1] is None else float(stored[1])
        status_code = stored[2] if stored else None
        payload_rows.append(
            {
                "student_id": student[0],
                "roll_number": student[1],
                "name": student[2],
                "marks": marks,
                "status_code": status_code,
            }
        )

    return {
        "session_label": session_label,
        "exam_name": exam_name,
        "section": section,
        "max_marks": max_marks,
        "locked": locked,
        "lock_message": LOCK_MESSAGE if locked else "",
        "student_count": len(payload_rows),
        "course": {
            "id": str(course[0]),
            "code": course[1],
            "name": course[2],
            "semester": course[3],
            "semester_label": ROMAN.get(course[3], str(course[3])),
            "course_type": _course_type(course[2]),
            "label": f"{course[2]} - {course[1]} (Sec - {section})",
        },
        "rows": payload_rows,
    }


def _normalize_row(row: MarkRowIn, max_marks: float) -> tuple[float | None, str | None]:
    status = (row.status_code or "").strip()
    if status:
        if status not in STATUS_CODES:
            raise HTTPException(status_code=400, detail="Status code must be 902, 903, or 904")
        return None, status
    if row.marks is None:
        return None, None
    if row.marks < 0 or row.marks > max_marks:
        raise HTTPException(
            status_code=400,
            detail=f"Marks must be between 0 and {max_marks:.2f}",
        )
    return round(float(row.marks), 2), None


def _save_rows(body: SaveMarksIn, teacher_id: str, lock: bool) -> dict[str, Any]:
    ensure_mark_entry_schema()
    _validate_filters(body.session_label, body.exam_name, body.section)
    max_marks = EXAMS[body.exam_name]

    with get_connection() as conn:
        course = _assert_course_owned(conn, body.course_id, teacher_id)
        students = _students_for(conn, body.course_id, body.section)
        if not students:
            raise HTTPException(status_code=400, detail="No students in this section")

        allowed = {row[0] for row in students}
        cleaned: dict[str, tuple[float | None, str | None]] = {}
        for row in body.rows:
            if row.student_id not in allowed:
                raise HTTPException(status_code=400, detail="A student on this sheet is not enrolled")
            cleaned[row.student_id] = _normalize_row(row, max_marks)

        if lock:
            missing = [roll for sid, roll, _name in students if not _has_value(cleaned.get(sid))]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="Enter marks or a status code for every student before locking",
                )

        sheet = _sheet_row(
            conn, teacher_id, body.course_id, body.session_label, body.exam_name, body.section
        )
        if sheet and sheet[1]:
            raise HTTPException(status_code=400, detail=LOCK_MESSAGE)
        if sheet:
            sheet_id = sheet[0]
        else:
            created = conn.execute(
                """
                INSERT INTO faculty_exam_sheets
                    (teacher_id, course_id, session_label, exam_name, section, locked)
                VALUES (%s, %s, %s, %s, %s, FALSE)
                RETURNING id
                """,
                (teacher_id, body.course_id, body.session_label, body.exam_name, body.section),
            ).fetchone()
            sheet_id = created[0]

        for student_id, (marks, status) in cleaned.items():
            conn.execute(
                """
                INSERT INTO faculty_exam_marks (sheet_id, student_id, marks_obtained, status_code)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sheet_id, student_id)
                DO UPDATE SET marks_obtained = EXCLUDED.marks_obtained,
                              status_code = EXCLUDED.status_code
                """,
                (sheet_id, student_id, marks, status),
            )

        if lock:
            conn.execute(
                """
                UPDATE faculty_exam_sheets
                SET locked = TRUE, updated_at = NOW()
                WHERE id = %s
                """,
                (sheet_id,),
            )
        else:
            conn.execute(
                "UPDATE faculty_exam_sheets SET updated_at = NOW() WHERE id = %s",
                (sheet_id,),
            )
        conn.commit()

    return {
        "ok": True,
        "locked": lock,
        "message": "Marks locked." if lock else "Marks saved.",
        "course_code": course[1],
    }


def _has_value(pair: tuple[float | None, str | None] | None) -> bool:
    if not pair:
        return False
    marks, status = pair
    return status is not None or marks is not None


@router.post("/save")
def save_marks(body: SaveMarksIn, access_token: str | None = Cookie(default=None)) -> dict[str, Any]:
    _teacher, teacher_id = _require_teacher(access_token)
    return _save_rows(body, teacher_id, lock=False)


@router.post("/lock")
def lock_marks(body: SaveMarksIn, access_token: str | None = Cookie(default=None)) -> dict[str, Any]:
    _teacher, teacher_id = _require_teacher(access_token)
    return _save_rows(body, teacher_id, lock=True)
