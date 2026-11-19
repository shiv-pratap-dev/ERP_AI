"""Faculty Extra Class Time Table API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from faculty_api import _require_teacher

router = APIRouter(prefix="/api/faculty/extra-class", tags=["extra-class"])

PLAN_TYPES = ("Core/Elective", "Open/Global Elective")
SESSIONS = ("July-Dec 2026-2027", "July-Dec 2025-2026")
TEACHING_FOR = ("Theory", "Practical", "Tutorial")
SECTIONS = ("S", "A", "B", "C")
BATCHES = {"S": ["S1", "S2"], "A": ["A1"], "B": ["B1"], "C": ["C1"]}
SLOT_TYPES = ("Morning New Slot", "Afternoon New Slot", "Regular Slot")
NEW_SLOT_TYPES = {"Morning New Slot", "Afternoon New Slot"}
SLOTS = {
    "Morning New Slot": (
        "CSE 001(08:50 AM-09:40 AM)",
        "CSE 002(09:40 AM-10:30 AM)",
        "CSE 003(02:20 PM-03:00 PM)",
        "CSE Lab(12:50 PM-02:00 PM)",
    ),
    "Afternoon New Slot": (
        "CSE 004(01:00 PM-01:50 PM)",
        "CSE 005(01:50 PM-02:40 PM)",
        "CSE 006(02:40 PM-03:30 PM)",
    ),
    "Regular Slot": (
        "08:50 AM - 09:40 AM",
        "09:40 AM - 10:30 AM",
        "10:40 AM - 11:30 AM",
        "11:30 AM - 12:20 PM",
        "12:05 PM - 12:50 PM",
        "01:00 PM - 01:50 PM",
    ),
}
ROOMS = ("CSE Lab 1", "CSE Lab 2", "Classroom 301", "Classroom 302", "Seminar Hall")
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}

_schema_ready = False


class ExtraClassIn(BaseModel):
    plan_type: str
    session_label: str
    course_details: str
    college_label: str
    course_id: str
    teaching_for: str
    section: str
    batch: str = ""
    slot_type: str
    slot_label: str
    room: str = ""
    regular_class: bool = False
    start_date: str = ""
    end_date: str = ""


class CancelIn(BaseModel):
    ids: list[str] = Field(min_length=1)


def ensure_extra_class_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return

    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faculty_extra_classes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                sr_no INTEGER NOT NULL,
                plan_type VARCHAR(40) NOT NULL,
                session_label VARCHAR(40) NOT NULL,
                course_details VARCHAR(250) NOT NULL,
                college_label VARCHAR(250) NOT NULL,
                semester SMALLINT NOT NULL,
                teaching_for VARCHAR(30) NOT NULL,
                section VARCHAR(10) NOT NULL,
                batch VARCHAR(20) NOT NULL DEFAULT '',
                slot_type VARCHAR(40) NOT NULL,
                slot_label VARCHAR(80) NOT NULL,
                room VARCHAR(80) NOT NULL DEFAULT '',
                regular_class BOOLEAN NOT NULL DEFAULT FALSE,
                start_date DATE,
                end_date DATE,
                status VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'cancelled')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_faculty_extra_classes_teacher
                ON faculty_extra_classes(teacher_id, status, sr_no)
            """
        )
        _seed_extra_classes(conn)
        conn.commit()

    _schema_ready = True


def _colleges_for(department: str) -> list[str]:
    dept = department or "Computer Science"
    return [
        f"SET - B. Tech-{dept}- 2023-24- Department of {dept} (New)",
        f"SET - Bachelor of Technology-{dept}- 2023-24- Department of {dept} (New)",
    ]


def _seed_extra_classes(conn) -> None:
    teachers = conn.execute(
        "SELECT id, full_name, employee_id, department FROM teachers"
    ).fetchall()
    for teacher_id, full_name, employee_id, department in teachers:
        exists = conn.execute(
            "SELECT 1 FROM faculty_extra_classes WHERE teacher_id = %s LIMIT 1",
            (teacher_id,),
        ).fetchone()
        if exists:
            continue
        courses = conn.execute(
            """
            SELECT id, course_name, semester
            FROM courses
            WHERE teacher_id = %s
            ORDER BY course_code
            """,
            (teacher_id,),
        ).fetchall()
        if not courses:
            continue
        college = _colleges_for(department)[0]
        details_2025 = f"July-Dec 2025-2026-{full_name}-{employee_id}"
        details_2026 = f"July-Dec 2026-2027-{full_name}-{employee_id}"
        first, second = courses[0], courses[min(1, len(courses) - 1)]
        samples = (
            (details_2025, "July-Dec 2025-2026", first, "S", "", "Morning New Slot",
             "CSE Lab(12:50 PM-02:00 PM)", date(2025, 12, 6), date(2025, 12, 6)),
            (details_2025, "July-Dec 2025-2026", first, "S", "", "Morning New Slot",
             "CSE 001(08:50 AM-09:40 AM)", date(2025, 12, 7), date(2025, 12, 7)),
            (details_2025, "July-Dec 2025-2026", second, "A", "A1", "Morning New Slot",
             "CSE 002(09:40 AM-10:30 AM)", date(2025, 12, 8), date(2025, 12, 8)),
            (details_2026, "July-Dec 2026-2027", first, "S", "S1", "Afternoon New Slot",
             "CSE 005(01:50 PM-02:40 PM)", date(2026, 11, 10), date(2026, 11, 12)),
            (details_2026, "July-Dec 2026-2027", second, "S", "S1", "Morning New Slot",
             "CSE 003(02:20 PM-03:00 PM)", date(2026, 11, 17), date(2026, 11, 19)),
        )
        for index, sample in enumerate(samples, start=1):
            details, session, course, section, batch, slot_type, slot, start, end = sample
            conn.execute(
                """
                INSERT INTO faculty_extra_classes (
                    teacher_id, course_id, sr_no, plan_type, session_label,
                    course_details, college_label, semester, teaching_for, section,
                    batch, slot_type, slot_label, room, regular_class, start_date, end_date
                )
                VALUES (
                    %s, %s, %s, 'Core/Elective', %s,
                    %s, %s, %s, 'Practical', %s,
                    %s, %s, %s, 'CSE Lab 1', FALSE, %s, %s
                )
                """,
                (
                    teacher_id, course[0], index, session,
                    details, college, course[2], section,
                    batch, slot_type, slot, start, end,
                ),
            )


def _fmt(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y")


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Enter a valid time table date")


def _row_payload(row) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "sr_no": int(row[1]),
        "plan_type": row[2],
        "session_label": row[3],
        "course_details": row[4],
        "college_label": row[5],
        "semester": int(row[6]),
        "semester_label": ROMAN.get(int(row[6]), str(row[6])),
        "teaching_for": row[7],
        "section": row[8],
        "batch": row[9] or "",
        "course_name": row[10],
        "course_code": row[11],
        "slot_type": row[12],
        "slot_label": row[13],
        "room": row[14] or "",
        "regular_class": bool(row[15]),
        "class_label": "Regular Class" if row[15] else "Extra Class",
        "start_date": _fmt(row[16]),
        "end_date": _fmt(row[17]),
        "status": row[18],
    }


def _list_rows(conn, teacher_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.id, e.sr_no, e.plan_type, e.session_label, e.course_details,
               e.college_label, e.semester, e.teaching_for, e.section, e.batch,
               c.course_name, c.course_code, e.slot_type, e.slot_label, e.room,
               e.regular_class, e.start_date, e.end_date, e.status
        FROM faculty_extra_classes e
        JOIN courses c ON c.id = e.course_id
        WHERE e.teacher_id = %s AND e.status = 'active'
        ORDER BY e.sr_no
        """,
        (teacher_id,),
    ).fetchall()
    return [_row_payload(row) for row in rows]


@router.get("/options")
def extra_class_options(access_token: str | None = Cookie(default=None)):
    ensure_extra_class_schema()
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
    semesters = []
    seen = set()
    course_rows = []
    for course_id, code, name, semester in courses:
        semester = int(semester)
        if semester not in seen:
            seen.add(semester)
            semesters.append({"value": semester, "label": ROMAN.get(semester, str(semester))})
        course_rows.append(
            {
                "id": str(course_id),
                "course_code": code,
                "course_name": name,
                "label": f"{code}-{name}",
                "semester": semester,
            }
        )
    return {
        "plan_types": list(PLAN_TYPES),
        "sessions": list(SESSIONS),
        "colleges": _colleges_for(teacher["department"]),
        "teacher_name": teacher["full_name"],
        "employee_id": teacher["employee_id"],
        "semesters": semesters,
        "courses": course_rows,
        "teaching_for": list(TEACHING_FOR),
        "sections": list(SECTIONS),
        "batches": BATCHES,
        "slot_types": list(SLOT_TYPES),
        "new_slot_types": sorted(NEW_SLOT_TYPES),
        "slots": {key: list(value) for key, value in SLOTS.items()},
        "rooms": list(ROOMS),
    }


@router.get("")
def list_extra_classes(access_token: str | None = Cookie(default=None)):
    ensure_extra_class_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        rows = _list_rows(conn, teacher_id)
    return {"rows": rows}


@router.post("")
def create_extra_class(
    body: ExtraClassIn,
    access_token: str | None = Cookie(default=None),
):
    ensure_extra_class_schema()
    teacher, teacher_id = _require_teacher(access_token)
    if body.plan_type not in PLAN_TYPES:
        raise HTTPException(status_code=400, detail="Select a course type")
    if body.session_label not in SESSIONS:
        raise HTTPException(status_code=400, detail="Select a session")
    if body.college_label not in _colleges_for(teacher["department"]):
        raise HTTPException(status_code=400, detail="Select a college")
    if body.teaching_for not in TEACHING_FOR:
        raise HTTPException(status_code=400, detail="Select a course type")
    if body.section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Select a section")
    if body.batch and body.batch not in BATCHES.get(body.section, []):
        raise HTTPException(status_code=400, detail="Select a batch for this section")
    if body.slot_type not in SLOT_TYPES:
        raise HTTPException(status_code=400, detail="Select a slot type")
    if body.slot_label not in SLOTS[body.slot_type]:
        raise HTTPException(status_code=400, detail="Select a slot")
    if body.room and body.room not in ROOMS:
        raise HTTPException(status_code=400, detail="Select a room")
    expected_details = f"{body.session_label}-{teacher['full_name']}-{teacher['employee_id']}"
    if body.course_details != expected_details:
        raise HTTPException(status_code=400, detail="Select course details")

    start = _parse_date(body.start_date)
    end = _parse_date(body.end_date)
    if body.slot_type in NEW_SLOT_TYPES:
        if not start or not end:
            raise HTTPException(status_code=400, detail="Select the time table start and end dates")
        if end < start:
            raise HTTPException(status_code=400, detail="Time table end date cannot be before the start date")

    with get_connection() as conn:
        course = conn.execute(
            """
            SELECT id, semester, course_name
            FROM courses
            WHERE id = %s AND teacher_id = %s
            """,
            (body.course_id, teacher_id),
        ).fetchone()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found for this teacher")
        duplicate = conn.execute(
            """
            SELECT 1 FROM faculty_extra_classes
            WHERE teacher_id = %s AND course_id = %s AND slot_label = %s
              AND start_date IS NOT DISTINCT FROM %s
              AND section = %s AND status = 'active'
            """,
            (teacher_id, body.course_id, body.slot_label, start, body.section),
        ).fetchone()
        if duplicate:
            raise HTTPException(
                status_code=400,
                detail="This extra class already exists for that slot and date",
            )
        sr_no = conn.execute(
            "SELECT COALESCE(MAX(sr_no), 0) + 1 FROM faculty_extra_classes WHERE teacher_id = %s",
            (teacher_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO faculty_extra_classes (
                teacher_id, course_id, sr_no, plan_type, session_label, course_details,
                college_label, semester, teaching_for, section, batch, slot_type,
                slot_label, room, regular_class, start_date, end_date
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                teacher_id, body.course_id, sr_no, body.plan_type, body.session_label,
                body.course_details, body.college_label, course[1], body.teaching_for,
                body.section, body.batch, body.slot_type, body.slot_label, body.room,
                body.regular_class, start, end,
            ),
        )
        conn.commit()
        rows = _list_rows(conn, teacher_id)

    return {"message": "Extra class time table created.", "rows": rows}


@router.post("/cancel")
def cancel_extra_classes(
    body: CancelIn,
    access_token: str | None = Cookie(default=None),
):
    ensure_extra_class_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        updated = conn.execute(
            """
            UPDATE faculty_extra_classes
            SET status = 'cancelled'
            WHERE teacher_id = %s AND status = 'active' AND id = ANY(%s)
            """,
            (teacher_id, body.ids),
        ).rowcount
        if not updated:
            raise HTTPException(status_code=404, detail="Select an active extra class to cancel")
        conn.commit()
        rows = _list_rows(conn, teacher_id)
    noun = "class" if updated == 1 else "classes"
    return {"message": f"Cancelled {updated} extra {noun}.", "rows": rows}
