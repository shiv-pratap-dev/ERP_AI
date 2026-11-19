"""Faculty Attendance By Faculty API.

Calendar of lectures, subject popup, and the present/absent grid.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from faculty_api import _require_teacher
from teaching_plan_api import ensure_teaching_plan_schema

router = APIRouter(prefix="/api/faculty/attendance", tags=["attendance"])

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
CLASS_TYPES = ("Regular Class", "Extra Class", "Tutorial")
ATTENDANCE_STATUSES = ("Completed", "Pending", "Not Held")
TOPIC_STATUSES = ("Not Started", "Started", "Completed")
WEEKDAY_PATTERNS = (
    {0, 2, 4, 5},  # Mon Wed Fri Sat
    {0, 1, 3, 4},  # Mon Tue Thu Fri
    {1, 3, 5},
)
SLOTS = (
    (1, "12:05 PM - 12:50 PM", "New Slot 6"),
    (2, "10:40 AM - 11:30 AM", "New Slot 4"),
    (3, "01:00 PM - 01:50 PM", "New Slot 7"),
)
LATE_RE = re.compile(r"^\d{1,2}:\d{2}$")
TERM_START = date(2026, 8, 26)
TERM_END = date(2026, 12, 9)

_schema_ready = False


class MarkIn(BaseModel):
    student_id: str
    mark: str
    late_time: str = ""


class SubmitIn(BaseModel):
    class_type: str
    attendance_status: str
    topic_key: str = Field(min_length=1)
    topic_covered_status: str
    marks: list[MarkIn]


def _today() -> date:
    return date.today()


def ensure_attendance_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return

    ensure_teaching_plan_schema()
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faculty_class_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                session_date DATE NOT NULL,
                slot_label VARCHAR(40) NOT NULL,
                slot_name VARCHAR(40) NOT NULL,
                slot_sort SMALLINT NOT NULL DEFAULT 1,
                section VARCHAR(10) NOT NULL DEFAULT 'S',
                course_type VARCHAR(20) NOT NULL DEFAULT 'CORE',
                lecture_type VARCHAR(20) NOT NULL DEFAULT 'REGULAR',
                class_type VARCHAR(40) NOT NULL DEFAULT 'Regular Class',
                attendance_status VARCHAR(20) NOT NULL DEFAULT 'Pending',
                topic_unit SMALLINT,
                topic_no SMALLINT,
                topic_title TEXT,
                topic_covered_status VARCHAR(20) NOT NULL DEFAULT 'Not Started',
                locked BOOLEAN NOT NULL DEFAULT FALSE,
                submitted_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (teacher_id, course_id, session_date, slot_label)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faculty_attendance_marks (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_id UUID NOT NULL
                    REFERENCES faculty_class_sessions(id) ON DELETE CASCADE,
                student_id UUID NOT NULL
                    REFERENCES students(id) ON DELETE CASCADE,
                mark VARCHAR(10) NOT NULL DEFAULT 'P'
                    CHECK (mark IN ('P', 'A', 'OD')),
                late_time VARCHAR(8) NOT NULL DEFAULT '',
                disciplinary BOOLEAN NOT NULL DEFAULT FALSE,
                UNIQUE (session_id, student_id)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_faculty_sessions_teacher_date
                ON faculty_class_sessions(teacher_id, session_date)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_faculty_marks_session
                ON faculty_attendance_marks(session_id)
            """
        )
        _seed_sessions(conn)
        conn.commit()

    _schema_ready = True


def _seed_sessions(conn) -> None:
    teachers = conn.execute("SELECT id FROM teachers").fetchall()
    students = conn.execute(
        "SELECT id FROM students ORDER BY roll_number"
    ).fetchall()
    student_ids = [row[0] for row in students]
    disciplinary_id = student_ids[-1] if student_ids else None
    od_id = student_ids[1] if len(student_ids) > 2 else None
    today = _today()

    for (teacher_id,) in teachers:
        exists = conn.execute(
            "SELECT 1 FROM faculty_class_sessions WHERE teacher_id = %s LIMIT 1",
            (teacher_id,),
        ).fetchone()
        if exists:
            continue

        courses = conn.execute(
            """
            SELECT id, course_code, course_name, semester
            FROM courses
            WHERE teacher_id = %s
            ORDER BY course_code
            """,
            (teacher_id,),
        ).fetchall()
        if not courses:
            continue

        mark_rows: list[tuple] = []
        topic_map = {
            course[0]: conn.execute(
                """
                SELECT unit_no, topic_no, topic_title
                FROM teaching_plan_topics
                WHERE course_id = %s
                ORDER BY unit_no, topic_no
                """,
                (course[0],),
            ).fetchall()
            for course in courses
        }
        day = TERM_START
        while day <= TERM_END:
            weekday = day.weekday()
            if weekday != 6:
                earlier_meets = False
                for index, course in enumerate(courses):
                    pattern = WEEKDAY_PATTERNS[index % len(WEEKDAY_PATTERNS)]
                    if weekday not in pattern:
                        continue
                    course_id = course[0]
                    slot_sort, slot_label, slot_name = SLOTS[index % len(SLOTS)]
                    topics = topic_map.get(course_id) or []
                    showcase = day == today and not earlier_meets
                    earlier_meets = True
                    completed = day < today or showcase
                    topic_unit = topic_no = None
                    topic_title = None
                    if topics and completed:
                        picked = topics[(day.toordinal() + index) % len(topics)]
                        topic_unit, topic_no, topic_title = picked
                    submitted_at = (
                        datetime.combine(day, time(12, 52, 27)) if completed else None
                    )
                    session_id = conn.execute(
                        """
                        INSERT INTO faculty_class_sessions (
                            teacher_id, course_id, session_date, slot_label, slot_name,
                            slot_sort, section, course_type, lecture_type, class_type,
                            attendance_status, topic_unit, topic_no, topic_title,
                            topic_covered_status, locked, submitted_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, 'S', 'CORE', 'REGULAR', 'Regular Class',
                            %s, %s, %s, %s,
                            %s, %s, %s
                        )
                        RETURNING id
                        """,
                        (
                            teacher_id,
                            course_id,
                            day,
                            slot_label,
                            slot_name,
                            slot_sort,
                            "Completed" if completed else "Pending",
                            topic_unit,
                            topic_no,
                            topic_title,
                            "Started" if completed else "Not Started",
                            completed,
                            submitted_at,
                        ),
                    ).fetchone()[0]

                    for student_index, student_id in enumerate(student_ids):
                        disciplinary = student_id == disciplinary_id
                        mark = _seed_mark(
                            student_id,
                            student_index,
                            day,
                            completed,
                            showcase,
                            disciplinary,
                            od_id,
                        )
                        late = ""
                        if mark == "P" and student_index % 8 == 3:
                            late = "05:00"
                        mark_rows.append(
                            (session_id, student_id, mark, late, disciplinary)
                        )
            day += timedelta(days=1)

        if mark_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO faculty_attendance_marks
                        (session_id, student_id, mark, late_time, disciplinary)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    mark_rows,
                )


def _seed_mark(
    student_id,
    student_index: int,
    session_date: date,
    completed: bool,
    showcase: bool,
    disciplinary: bool,
    od_id,
) -> str:
    if disciplinary:
        return "A"
    if not completed:
        if student_id == od_id and session_date == _today() and not showcase:
            return "OD"
        return "P"
    if student_id == od_id and session_date.day % 9 == 0 and not showcase:
        return "OD"
    if (student_index + session_date.toordinal()) % 5 == 0:
        return "A"
    return "P"


def _subject_line(code: str, name: str, semester: int, slot_label: str, slot_name: str) -> str:
    roman = ROMAN.get(int(semester or 1), str(semester))
    return (
        f"{code} - {name} - CSE-{roman} - ({slot_label}) - {slot_name}"
    )


def _fmt_stamp(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y %H:%M:%S")


def _fmt_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _topic_key(unit_no, topic_no) -> str:
    if unit_no is None or topic_no is None:
        return ""
    return f"{int(unit_no)}-{int(topic_no)}"


def _topic_label(unit_no, topic_no, title: str | None) -> str:
    if unit_no is None or topic_no is None:
        return ""
    return f"{int(unit_no)} - {int(topic_no)} - {title or ''}".strip()


def _rrno(roll: str) -> str:
    roll = roll or ""
    return roll[-4:] if len(roll) > 4 else roll


def _tone(mark: str, disciplinary: bool) -> str:
    if disciplinary or mark == "A":
        return "red"
    if mark == "OD":
        return "green"
    return "blue"


def _counts(marks: list[str]) -> dict[str, int]:
    return {
        "total": len(marks),
        "present": sum(1 for mark in marks if mark == "P"),
        "absent": sum(1 for mark in marks if mark == "A"),
        "od": sum(1 for mark in marks if mark == "OD"),
    }


def _load_session(conn, session_id: str, teacher_id: str):
    row = conn.execute(
        """
        SELECT s.id, s.session_date, s.slot_label, s.slot_name, s.slot_sort,
               s.section, s.course_type, s.lecture_type, s.class_type,
               s.attendance_status, s.topic_unit, s.topic_no, s.topic_title,
               s.topic_covered_status, s.locked, s.submitted_at,
               c.id, c.course_code, c.course_name, c.semester,
               t.full_name
        FROM faculty_class_sessions s
        JOIN courses c ON c.id = s.course_id
        JOIN teachers t ON t.id = s.teacher_id
        WHERE s.id = %s AND s.teacher_id = %s
        """,
        (session_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lecture not found")
    return row


def _calendar_item(row, teacher_name: str, today: date) -> dict[str, Any]:
    session_date = row[1]
    submitted_at = row[15]
    status = "COMPLETE" if submitted_at and row[9] == "Completed" else "PENDING"
    return {
        "id": str(row[0]),
        "date": session_date.isoformat(),
        "date_label": _fmt_date(session_date),
        "course_code": row[17],
        "course_name": row[18],
        "subject_line": _subject_line(row[17], row[18], row[19], row[2], row[3]),
        "slot_label": row[2],
        "slot_name": row[3],
        "section": row[5],
        "course_type": row[6],
        "lecture_type": row[7],
        "status": status,
        "locked": bool(row[14]),
        "submitted": submitted_at is not None,
        "submitted_by": teacher_name if submitted_at else "",
        "submitted_date": _fmt_stamp(submitted_at),
        "tone": "orange" if session_date > today else "green",
    }


def _history_label(session_date: date, slot_label: str) -> str:
    return f"{session_date.strftime('%d-%m-%Y')} {slot_label}"


@router.get("/calendar")
def attendance_calendar(
    year: int | None = None,
    month: int | None = None,
    access_token: str | None = Cookie(default=None),
):
    ensure_attendance_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    today = _today()
    year = year or today.year
    month = month or today.month
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="Month must be between 1 and 12")

    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    prev_month = month - 1 or 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year

    with get_connection() as conn:
        teacher_name = conn.execute(
            "SELECT full_name FROM teachers WHERE id = %s",
            (teacher_id,),
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT s.id, s.session_date, s.slot_label, s.slot_name, s.slot_sort,
                   s.section, s.course_type, s.lecture_type, s.class_type,
                   s.attendance_status, s.topic_unit, s.topic_no, s.topic_title,
                   s.topic_covered_status, s.locked, s.submitted_at,
                   c.id, c.course_code, c.course_name, c.semester
            FROM faculty_class_sessions s
            JOIN courses c ON c.id = s.course_id
            WHERE s.teacher_id = %s
              AND s.session_date >= %s
              AND s.session_date < %s
            ORDER BY s.session_date, s.slot_sort, c.course_code
            """,
            (teacher_id, start, end),
        ).fetchall()

    days: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        item = _calendar_item(row, teacher_name, today)
        days.setdefault(item["date"], []).append(item)

    return {
        "year": year,
        "month": month,
        "month_name": MONTHS[month - 1],
        "today": today.isoformat(),
        "prev": {
            "year": prev_year,
            "month": prev_month,
            "name": MONTHS[prev_month - 1],
        },
        "next": {
            "year": next_year,
            "month": next_month,
            "name": MONTHS[next_month - 1],
        },
        "days": days,
    }


def _sheet_payload(conn, row, teacher_id: str) -> dict[str, Any]:
    session_id = row[0]
    session_date = row[1]
    course_id = row[16]
    teacher_name = row[20]
    topics = conn.execute(
        """
        SELECT unit_no, topic_no, topic_title
        FROM teaching_plan_topics
        WHERE course_id = %s
        ORDER BY unit_no, topic_no
        """,
        (course_id,),
    ).fetchall()
    history_rows = conn.execute(
        """
        SELECT id, session_date, slot_label
        FROM faculty_class_sessions
        WHERE teacher_id = %s
          AND course_id = %s
          AND slot_label = %s
          AND session_date < %s
          AND submitted_at IS NOT NULL
        ORDER BY session_date DESC
        LIMIT 7
        """,
        (teacher_id, course_id, row[2], session_date),
    ).fetchall()
    history = [
        {
            "id": str(item[0]),
            "label": _history_label(item[1], item[2]),
        }
        for item in history_rows
    ]
    history_ids = [item[0] for item in history_rows]
    history_marks: dict[tuple, str] = {}
    if history_ids:
        for session_key, student_key, mark in conn.execute(
            """
            SELECT session_id, student_id, mark
            FROM faculty_attendance_marks
            WHERE session_id = ANY(%s)
            """,
            (history_ids,),
        ).fetchall():
            history_marks[(str(session_key), str(student_key))] = mark

    students = []
    marks = []
    for student in conn.execute(
        """
        SELECT s.id, s.roll_number, s.full_name,
               COALESCE(m.mark, 'P'),
               COALESCE(m.late_time, ''),
               COALESCE(m.disciplinary, FALSE)
        FROM enrollments e
        JOIN students s ON s.id = e.student_id
        LEFT JOIN faculty_attendance_marks m
          ON m.student_id = s.id AND m.session_id = %s
        WHERE e.course_id = %s
        ORDER BY s.roll_number
        """,
        (session_id, course_id),
    ).fetchall():
        mark = student[3]
        disciplinary = bool(student[5])
        if disciplinary:
            mark = "A"
        student_id = str(student[0])
        marks.append(mark)
        students.append(
            {
                "id": student_id,
                "roll_number": student[1],
                "rrno": _rrno(student[1]),
                "full_name": student[2],
                "mark": mark,
                "late_time": student[4] if mark == "P" else "",
                "disciplinary": disciplinary,
                "tone": _tone(mark, disciplinary),
                "history": {
                    item["id"]: history_marks.get((item["id"], student_id), "")
                    for item in history
                },
            }
        )

    counts = _counts(marks)
    status = "COMPLETE" if row[15] and row[9] == "Completed" else "PENDING"
    return {
        "session": {
            "id": str(session_id),
            "date": session_date.isoformat(),
            "date_label": _fmt_date(session_date),
            "subject_line": _subject_line(row[17], row[18], row[19], row[2], row[3]),
            "course_code": row[17],
            "slot_label": row[2],
            "slot_name": row[3],
            "section": row[5],
            "course_type": row[6],
            "lecture_type": row[7],
            "class_type": row[8] or "Regular Class",
            "attendance_status": row[9] or "Pending",
            "topic_key": _topic_key(row[10], row[11]),
            "topic_label": _topic_label(row[10], row[11], row[12]),
            "topic_covered_status": row[13] or "Not Started",
            "locked": bool(row[14]),
            "status": status,
            "submitted_by": teacher_name if row[15] else "",
            "submitted_date": _fmt_stamp(row[15]),
            "counts": counts,
        },
        "topics": [
            {
                "key": _topic_key(unit_no, topic_no),
                "label": _topic_label(unit_no, topic_no, title),
            }
            for unit_no, topic_no, title in topics
        ],
        "class_types": list(CLASS_TYPES),
        "attendance_statuses": list(ATTENDANCE_STATUSES),
        "topic_statuses": list(TOPIC_STATUSES),
        "history": history,
        "students": students,
    }


@router.get("/sessions/{session_id}")
def attendance_sheet(
    session_id: str,
    access_token: str | None = Cookie(default=None),
):
    ensure_attendance_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        row = _load_session(conn, session_id, teacher_id)
        return _sheet_payload(conn, row, teacher_id)


@router.post("/sessions/{session_id}")
def submit_attendance(
    session_id: str,
    body: SubmitIn,
    access_token: str | None = Cookie(default=None),
):
    ensure_attendance_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    if body.class_type not in CLASS_TYPES:
        raise HTTPException(status_code=400, detail="Select a class type")
    if body.attendance_status not in ATTENDANCE_STATUSES:
        raise HTTPException(status_code=400, detail="Select an attendance status")
    if body.topic_covered_status not in TOPIC_STATUSES:
        raise HTTPException(status_code=400, detail="Select a topic covered status")

    with get_connection() as conn:
        row = _load_session(conn, session_id, teacher_id)
        if row[14]:
            raise HTTPException(
                status_code=409,
                detail="Attendance is locked. Unlock it before submitting changes.",
            )
        course_id = row[16]
        topic = conn.execute(
            """
            SELECT unit_no, topic_no, topic_title
            FROM teaching_plan_topics
            WHERE course_id = %s
              AND (unit_no::text || '-' || topic_no::text) = %s
            """,
            (course_id, body.topic_key),
        ).fetchone()
        if not topic:
            raise HTTPException(status_code=400, detail="Select the topic covered")

        enrolled = conn.execute(
            """
            SELECT s.id, COALESCE(m.disciplinary, FALSE)
            FROM enrollments e
            JOIN students s ON s.id = e.student_id
            LEFT JOIN faculty_attendance_marks m
              ON m.student_id = s.id AND m.session_id = %s
            WHERE e.course_id = %s
            """,
            (session_id, course_id),
        ).fetchall()
        enrolled_map = {str(student_id): bool(flag) for student_id, flag in enrolled}
        posted: dict[str, MarkIn] = {}
        for item in body.marks:
            if item.student_id not in enrolled_map:
                raise HTTPException(status_code=400, detail="Unknown student in this class")
            if item.mark not in ("P", "A", "OD"):
                raise HTTPException(status_code=400, detail="Attendance mark must be P, A, or OD")
            late = (item.late_time or "").strip()
            if late and not LATE_RE.match(late):
                raise HTTPException(status_code=400, detail="Late by time must look like MM:SS")
            posted[item.student_id] = item

        missing = [student_id for student_id in enrolled_map if student_id not in posted]
        if missing:
            raise HTTPException(status_code=400, detail="Mark every student before submitting")

        for student_id, item in posted.items():
            disciplinary = enrolled_map[student_id]
            mark = "A" if disciplinary else item.mark
            late = "" if mark != "P" else (item.late_time or "").strip()
            conn.execute(
                """
                INSERT INTO faculty_attendance_marks
                    (session_id, student_id, mark, late_time, disciplinary)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (session_id, student_id)
                DO UPDATE SET mark = EXCLUDED.mark, late_time = EXCLUDED.late_time
                """,
                (session_id, student_id, mark, late, disciplinary),
            )

        conn.execute(
            """
            UPDATE faculty_class_sessions
            SET class_type = %s,
                attendance_status = %s,
                topic_unit = %s,
                topic_no = %s,
                topic_title = %s,
                topic_covered_status = %s,
                locked = TRUE,
                submitted_at = NOW()
            WHERE id = %s
            """,
            (
                body.class_type,
                body.attendance_status,
                topic[0],
                topic[1],
                topic[2],
                body.topic_covered_status,
                session_id,
            ),
        )
        conn.commit()
        fresh = _load_session(conn, session_id, teacher_id)
        payload = _sheet_payload(conn, fresh, teacher_id)

    payload["message"] = "Attendance submitted."
    return payload


def _set_lock(session_id: str, teacher_id: str, locked: bool) -> dict[str, Any]:
    with get_connection() as conn:
        row = _load_session(conn, session_id, teacher_id)
        if locked and row[15] is None:
            raise HTTPException(status_code=400, detail="Submit attendance before locking it")
        conn.execute(
            "UPDATE faculty_class_sessions SET locked = %s WHERE id = %s",
            (locked, session_id),
        )
        conn.commit()
        fresh = _load_session(conn, session_id, teacher_id)
        teacher_name = fresh[20]
        return {"session": _calendar_item(fresh, teacher_name, _today()), "sheet": _sheet_payload(conn, fresh, teacher_id)}


@router.post("/sessions/{session_id}/lock")
def lock_attendance(
    session_id: str,
    access_token: str | None = Cookie(default=None),
):
    ensure_attendance_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    return _set_lock(session_id, teacher_id, True)


@router.post("/sessions/{session_id}/unlock")
def unlock_attendance(
    session_id: str,
    access_token: str | None = Cookie(default=None),
):
    ensure_attendance_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    return _set_lock(session_id, teacher_id, False)
