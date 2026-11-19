"""Faculty Teaching Plan Entry API."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Json
from pydantic import BaseModel, Field, ValidationError

from faculty_api import _assert_course_owned, _require_teacher

router = APIRouter(prefix="/api/faculty/teaching-plan", tags=["teaching-plan"])

SESSIONS = ("July-Dec 2026-2027", "July-Dec 2025-2026")
TEACHING_FOR = ("Theory", "Practical", "Tutorial")
SECTIONS = ("S", "A", "B", "C")
PLAN_TYPES = ("core", "elective")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
LECTURE_SLOTS = (
    "08:50 AM - 09:40 AM",
    "09:40 AM - 10:30 AM",
    "10:40 AM - 11:30 AM",
    "11:30 AM - 12:20 PM",
    "01:00 PM - 01:50 PM",
    "01:50 PM - 02:40 PM",
    "02:40 PM - 03:30 PM",
    "03:30 PM - 04:20 PM",
)
TIMETABLE_RANGES = {
    "July-Dec 2026-2027": (
        "26/08/2026 - 09/12/2026",
        "02/09/2026 - 09/12/2026",
        "14/09/2026 - 09/12/2026",
        "28/09/2026 - 09/12/2026",
        "12/10/2026 - 09/12/2026",
    ),
    "July-Dec 2025-2026": (
        "26/08/2025 - 09/12/2025",
        "02/09/2025 - 09/12/2025",
        "14/09/2025 - 09/12/2025",
        "28/09/2025 - 09/12/2025",
        "12/10/2025 - 09/12/2025",
    ),
}
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}

_schema_ready = False


def _syllabus_for(course_code: str, course_name: str) -> dict[int, list[str]]:
    if course_code == "CS301":
        return {
            1: [
                "Unit 1 Introduction Time complexity",
                "Arrays, stacks and queues",
                "Linked lists",
                "Recursion and backtracking",
            ],
            2: [
                "Divide AND Conquer",
                "Merge sort and quick sort",
                "Searching techniques",
                "Greedy algorithms",
                "Dynamic programming",
                "Longest Common subsequence",
            ],
            3: [
                "Graph representation and traversal",
                "Graph, maximum flow cont.",
                "Shortest path algorithms",
                "Minimum spanning trees",
            ],
            4: [
                "Hashing",
                "Heaps and priority queues",
                "String matching",
                "Unit 4 review",
            ],
            5: [
                "Complexity classes",
                "NP completeness overview",
                "Approximation methods",
                "Unit 5 review",
            ],
            6: [
                "Revision of units 1-3",
                "Revision of units 4-5",
                "Previous exam problems",
                "Course wrap-up",
            ],
        }

    name = course_name or course_code
    return {
        1: [
            f"Introduction to {name}",
            "Scope and basic terminology",
            "Foundational examples",
            "Unit 1 review",
        ],
        2: [
            f"Core concepts of {name}",
            "Methods and techniques",
            "Worked problems",
            "Comparisons",
            "Case discussion",
            "Unit 2 review",
        ],
        3: [
            "Advanced topics",
            "Applications",
            "Practice set",
            "Unit 3 review",
        ],
        4: [
            "Design issues",
            "Implementation notes",
            "Tools and standards",
            "Unit 4 review",
        ],
        5: [
            "Integration topics",
            "Mini case",
            "Performance considerations",
            "Unit 5 review",
        ],
        6: [
            "Revision",
            "Previous patterns",
            "Open problems",
            "Course wrap-up",
        ],
    }


def _colleges_for(department: str) -> list[str]:
    dept = department or "Computer Science"
    return [
        f"SET - B. Tech-{dept}- 2023-24- Department of {dept} (New)",
        f"SET - Bachelor of Technology-{dept}- 2023-24- Department of {dept} (New)",
    ]


def _pretty(value: date | None) -> str:
    if not value:
        return ""
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _parse_dmy(value: str) -> date:
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()


def _parse_user_date(value: str) -> date:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty date")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date '{raw}'")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def ensure_teaching_plan_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return

    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teaching_plan_topics (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                unit_no SMALLINT NOT NULL CHECK (unit_no BETWEEN 1 AND 6),
                topic_no SMALLINT NOT NULL CHECK (topic_no > 0),
                topic_title TEXT NOT NULL,
                UNIQUE (course_id, unit_no, topic_no)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teaching_plans (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                plan_type VARCHAR(20) NOT NULL DEFAULT 'core'
                    CHECK (plan_type IN ('core', 'elective')),
                session_label VARCHAR(40) NOT NULL,
                college_label VARCHAR(250) NOT NULL,
                teaching_for VARCHAR(30) NOT NULL DEFAULT 'Theory',
                section VARCHAR(10) NOT NULL,
                unit_no SMALLINT NOT NULL CHECK (unit_no BETWEEN 1 AND 6),
                topic_no SMALLINT NOT NULL CHECK (topic_no > 0),
                topic_description TEXT NOT NULL,
                sessions_required SMALLINT NOT NULL CHECK (sessions_required > 0),
                timetable_range VARCHAR(40) NOT NULL,
                proposed_date DATE,
                conduct_date DATE,
                lecture_slot VARCHAR(40),
                proposed_dates JSONB NOT NULL DEFAULT '{}'::jsonb,
                lecture_slots JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_teaching_plans_teacher
                ON teaching_plans(teacher_id, plan_type, proposed_date)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_teaching_plan_topics_course
                ON teaching_plan_topics(course_id, unit_no, topic_no)
            """
        )

        courses = conn.execute(
            "SELECT id, course_code, course_name FROM courses ORDER BY course_code"
        ).fetchall()
        for course_id, course_code, course_name in courses:
            exists = conn.execute(
                "SELECT 1 FROM teaching_plan_topics WHERE course_id = %s LIMIT 1",
                (course_id,),
            ).fetchone()
            if exists:
                continue
            for unit_no, titles in _syllabus_for(course_code, course_name).items():
                for topic_no, title in enumerate(titles, start=1):
                    conn.execute(
                        """
                        INSERT INTO teaching_plan_topics
                            (course_id, unit_no, topic_no, topic_title)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (course_id, unit_no, topic_no) DO NOTHING
                        """,
                        (course_id, unit_no, topic_no, title),
                    )

        _seed_demo_plans(conn)
        conn.commit()

    _schema_ready = True


def _seed_demo_plans(conn) -> None:
    row = conn.execute(
        """
        SELECT t.id, c.id
        FROM teachers t
        JOIN courses c ON c.teacher_id = t.id
        WHERE t.employee_id = 'EMP001' AND c.course_code = 'CS301'
        """
    ).fetchone()
    if not row:
        return
    teacher_id, course_id = row
    existing = conn.execute(
        "SELECT 1 FROM teaching_plans WHERE teacher_id = %s LIMIT 1",
        (teacher_id,),
    ).fetchone()
    if existing:
        return

    college = _colleges_for("Computer Science")[0]
    samples = (
        (3, 2, 2, date(2026, 10, 12), date(2026, 10, 12)),
        (3, 2, 2, date(2026, 10, 12), date(2026, 10, 14)),
        (1, 1, 2, date(2026, 8, 26), date(2026, 8, 26)),
        (1, 1, 2, date(2026, 8, 26), date(2026, 8, 26)),
        (2, 6, 1, date(2026, 8, 26), date(2026, 8, 18)),
        (2, 6, 1, date(2026, 8, 26), date(2026, 9, 2)),
    )
    for unit_no, topic_no, sessions, proposed, conduct in samples:
        topic = conn.execute(
            """
            SELECT topic_title FROM teaching_plan_topics
            WHERE course_id = %s AND unit_no = %s AND topic_no = %s
            """,
            (course_id, unit_no, topic_no),
        ).fetchone()
        if not topic:
            continue
        weekday = proposed.strftime("%A").lower()
        conn.execute(
            """
            INSERT INTO teaching_plans (
                teacher_id, course_id, plan_type, session_label, college_label,
                teaching_for, section, unit_no, topic_no, topic_description,
                sessions_required, timetable_range, proposed_date, conduct_date,
                lecture_slot, proposed_dates, lecture_slots
            )
            VALUES (
                %s, %s, 'core', %s, %s,
                'Theory', 'S', %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                teacher_id,
                course_id,
                SESSIONS[0],
                college,
                unit_no,
                topic_no,
                topic[0],
                sessions,
                TIMETABLE_RANGES[SESSIONS[0]][0],
                proposed,
                conduct,
                LECTURE_SLOTS[2],
                Json({weekday: proposed.isoformat()}),
                Json({weekday: LECTURE_SLOTS[2]}),
            ),
        )


def _teacher_courses(conn, teacher_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, course_code, course_name, semester
        FROM courses
        WHERE teacher_id = %s
        ORDER BY course_code
        """,
        (teacher_id,),
    ).fetchall()
    total = len(rows)
    courses = []
    for index, row in enumerate(rows):
        if total <= 1:
            plan_type = "core"
        else:
            plan_type = "elective" if index >= (total + 1) // 2 else "core"
        courses.append(
            {
                "id": str(row[0]),
                "course_code": row[1],
                "course_name": row[2],
                "semester": int(row[3]),
                "plan_type": plan_type,
                "label": f"{ROMAN.get(int(row[3]), row[3])} - Theory - {row[1]} - {row[2]}",
            }
        )
    return courses


def _course_allowed(courses: list[dict[str, Any]], course_id: str, plan_type: str) -> bool:
    return any(c["id"] == course_id and c["plan_type"] == plan_type for c in courses)


class TeachingPlanIn(BaseModel):
    plan_type: str = "core"
    session_label: str
    college_label: str
    course_id: str
    teaching_for: str
    section: str
    unit_no: int = Field(ge=1, le=6)
    topic_no: int = Field(ge=1, le=20)
    topic_description: str = ""
    sessions_required: int = Field(ge=1, le=40)
    timetable_range: str
    proposed_dates: dict[str, str] = Field(default_factory=dict)
    lecture_slots: dict[str, str] = Field(default_factory=dict)
    conduct_date: str | None = None


def _normalize_plan(data: TeachingPlanIn, courses: list[dict[str, Any]]) -> dict[str, Any]:
    plan_type = data.plan_type.strip().lower()
    if plan_type not in PLAN_TYPES:
        raise HTTPException(status_code=400, detail="Invalid teaching plan tab")
    if data.session_label not in SESSIONS:
        raise HTTPException(status_code=400, detail="Select a valid session")
    if data.teaching_for not in TEACHING_FOR:
        raise HTTPException(status_code=400, detail="Select what the teaching plan is for")
    if data.section not in SECTIONS:
        raise HTTPException(status_code=400, detail="Select a section")
    if data.timetable_range not in TIMETABLE_RANGES[data.session_label]:
        raise HTTPException(status_code=400, detail="Select a time table date range")
    if not _course_allowed(courses, data.course_id, plan_type):
        raise HTTPException(status_code=400, detail="Course is not available on this tab")

    college = data.college_label.strip()
    if not college:
        raise HTTPException(status_code=400, detail="Select a college")

    proposed: dict[str, str] = {}
    start, end = _parse_dmy(data.timetable_range.split(" - ")[0]), _parse_dmy(
        data.timetable_range.split(" - ")[1]
    )
    for key, raw in data.proposed_dates.items():
        if key not in WEEKDAYS or not str(raw).strip():
            continue
        try:
            parsed = _parse_user_date(str(raw))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid proposed date") from exc
        if parsed < start or parsed > end:
            raise HTTPException(
                status_code=400,
                detail="Proposed date must fall inside the selected time table range",
            )
        proposed[key] = parsed.isoformat()

    if not proposed:
        raise HTTPException(status_code=400, detail="Select at least one proposed lecture date")

    slots: dict[str, str] = {}
    for key, raw in data.lecture_slots.items():
        slot = str(raw).strip()
        if key not in WEEKDAYS or not slot:
            continue
        if slot not in LECTURE_SLOTS:
            raise HTTPException(status_code=400, detail="Select a valid lecture slot")
        slots[key] = slot

    conduct = None
    if data.conduct_date and str(data.conduct_date).strip():
        try:
            conduct = _parse_user_date(str(data.conduct_date))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid conduct date") from exc

    proposed_date = min(date.fromisoformat(value) for value in proposed.values())
    lecture_slot = next(iter(slots.values()), None)
    description = data.topic_description.strip()
    return {
        "plan_type": plan_type,
        "session_label": data.session_label,
        "college_label": college[:250],
        "course_id": data.course_id,
        "teaching_for": data.teaching_for,
        "section": data.section,
        "unit_no": data.unit_no,
        "topic_no": data.topic_no,
        "topic_description": description[:500],
        "sessions_required": data.sessions_required,
        "timetable_range": data.timetable_range,
        "proposed_date": proposed_date,
        "conduct_date": conduct,
        "lecture_slot": lecture_slot,
        "proposed_dates": proposed,
        "lecture_slots": slots,
    }


def _topic_title(conn, course_id: str, unit_no: int, topic_no: int) -> str:
    row = conn.execute(
        """
        SELECT topic_title FROM teaching_plan_topics
        WHERE course_id = %s AND unit_no = %s AND topic_no = %s
        """,
        (course_id, unit_no, topic_no),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Topic number is not in this unit")
    return row[0]


def _plan_from_row(row: tuple) -> dict[str, Any]:
    proposed = row[14]
    conduct = row[15]
    return {
        "id": str(row[0]),
        "plan_type": row[1],
        "session_label": row[2],
        "college_label": row[3],
        "course_id": str(row[4]),
        "course_code": row[5],
        "course_name": row[6],
        "semester": int(row[7]),
        "teaching_for": row[8],
        "section": row[9],
        "unit_no": int(row[10]),
        "topic_no": int(row[11]),
        "topic_description": row[12],
        "sessions_required": int(row[13]),
        "timetable_range": row[16],
        "proposed_date": proposed.isoformat() if proposed else None,
        "proposed_date_label": _pretty(proposed),
        "conduct_date": conduct.isoformat() if conduct else None,
        "conduct_date_label": _pretty(conduct),
        "lecture_slot": row[17] or "",
        "proposed_dates": _as_dict(row[18]),
        "lecture_slots": _as_dict(row[19]),
        "status": "Executed" if conduct else "Planned",
    }


_PLAN_SQL = """
    SELECT p.id, p.plan_type, p.session_label, p.college_label, p.course_id,
           c.course_code, c.course_name, c.semester,
           p.teaching_for, p.section, p.unit_no, p.topic_no, p.topic_description,
           p.sessions_required, p.proposed_date, p.conduct_date, p.timetable_range,
           p.lecture_slot, p.proposed_dates, p.lecture_slots
    FROM teaching_plans p
    JOIN courses c ON c.id = p.course_id
"""


def _list_plans(
    conn,
    teacher_id: str,
    plan_type: str,
    course_id: str | None,
    section: str | None,
) -> list[dict[str, Any]]:
    sql = _PLAN_SQL + " WHERE p.teacher_id = %s AND p.plan_type = %s"
    params: list[Any] = [teacher_id, plan_type]
    if course_id:
        sql += " AND p.course_id = %s"
        params.append(course_id)
    if section:
        sql += " AND p.section = %s"
        params.append(section)
    sql += " ORDER BY p.proposed_date NULLS LAST, p.unit_no, p.topic_no, p.created_at"
    return [_plan_from_row(row) for row in conn.execute(sql, params).fetchall()]


@router.get("/options")
def teaching_plan_options(access_token: str | None = Cookie(default=None)):
    ensure_teaching_plan_schema()
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        courses = _teacher_courses(conn, teacher_id)
    return {
        "sessions": list(SESSIONS),
        "colleges": _colleges_for(teacher["department"]),
        "teaching_for": list(TEACHING_FOR),
        "sections": list(SECTIONS),
        "units": [1, 2, 3, 4, 5, 6],
        "lecture_slots": list(LECTURE_SLOTS),
        "timetable_ranges": {key: list(value) for key, value in TIMETABLE_RANGES.items()},
        "roman": {str(key): value for key, value in ROMAN.items()},
        "courses": courses,
    }


@router.get("/topics")
def teaching_plan_topics(
    course_id: str,
    unit_no: int,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    if unit_no < 1 or unit_no > 6:
        raise HTTPException(status_code=400, detail="Unit must be between 1 and 6")
    with get_connection() as conn:
        _assert_course_owned(conn, course_id, teacher_id)
        rows = conn.execute(
            """
            SELECT topic_no, topic_title
            FROM teaching_plan_topics
            WHERE course_id = %s AND unit_no = %s
            ORDER BY topic_no
            """,
            (course_id, unit_no),
        ).fetchall()
    return {
        "topics": [{"topic_no": int(row[0]), "topic_title": row[1]} for row in rows]
    }


@router.get("")
def list_teaching_plans(
    plan_type: str = "core",
    course_id: str | None = None,
    section: str | None = None,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    if plan_type not in PLAN_TYPES:
        raise HTTPException(status_code=400, detail="Invalid teaching plan tab")
    with get_connection() as conn:
        if course_id:
            _assert_course_owned(conn, course_id, teacher_id)
        plans = _list_plans(conn, teacher_id, plan_type, course_id, section or None)
    return {"plans": plans}


@router.post("")
def create_teaching_plan(
    data: TeachingPlanIn,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        courses = _teacher_courses(conn, teacher_id)
        payload = _normalize_plan(data, courses)
        _assert_course_owned(conn, payload["course_id"], teacher_id)
        title = _topic_title(conn, payload["course_id"], payload["unit_no"], payload["topic_no"])
        if not payload["topic_description"]:
            payload["topic_description"] = title
        row = conn.execute(
            """
            INSERT INTO teaching_plans (
                teacher_id, course_id, plan_type, session_label, college_label,
                teaching_for, section, unit_no, topic_no, topic_description,
                sessions_required, timetable_range, proposed_date, conduct_date,
                lecture_slot, proposed_dates, lecture_slots
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id
            """,
            (
                teacher_id,
                payload["course_id"],
                payload["plan_type"],
                payload["session_label"],
                payload["college_label"],
                payload["teaching_for"],
                payload["section"],
                payload["unit_no"],
                payload["topic_no"],
                payload["topic_description"],
                payload["sessions_required"],
                payload["timetable_range"],
                payload["proposed_date"],
                payload["conduct_date"],
                payload["lecture_slot"],
                Json(payload["proposed_dates"]),
                Json(payload["lecture_slots"]),
            ),
        ).fetchone()
        conn.commit()
    return {"message": "Teaching plan saved", "id": str(row[0])}


@router.put("/{plan_id}")
def update_teaching_plan(
    plan_id: str,
    data: TeachingPlanIn,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        owned = conn.execute(
            "SELECT id FROM teaching_plans WHERE id = %s AND teacher_id = %s",
            (plan_id, teacher_id),
        ).fetchone()
        if not owned:
            raise HTTPException(status_code=404, detail="Teaching plan not found")
        courses = _teacher_courses(conn, teacher_id)
        payload = _normalize_plan(data, courses)
        _assert_course_owned(conn, payload["course_id"], teacher_id)
        title = _topic_title(conn, payload["course_id"], payload["unit_no"], payload["topic_no"])
        if not payload["topic_description"]:
            payload["topic_description"] = title
        conn.execute(
            """
            UPDATE teaching_plans SET
                course_id = %s,
                plan_type = %s,
                session_label = %s,
                college_label = %s,
                teaching_for = %s,
                section = %s,
                unit_no = %s,
                topic_no = %s,
                topic_description = %s,
                sessions_required = %s,
                timetable_range = %s,
                proposed_date = %s,
                conduct_date = %s,
                lecture_slot = %s,
                proposed_dates = %s,
                lecture_slots = %s,
                updated_at = NOW()
            WHERE id = %s AND teacher_id = %s
            """,
            (
                payload["course_id"],
                payload["plan_type"],
                payload["session_label"],
                payload["college_label"],
                payload["teaching_for"],
                payload["section"],
                payload["unit_no"],
                payload["topic_no"],
                payload["topic_description"],
                payload["sessions_required"],
                payload["timetable_range"],
                payload["proposed_date"],
                payload["conduct_date"],
                payload["lecture_slot"],
                Json(payload["proposed_dates"]),
                Json(payload["lecture_slots"]),
                plan_id,
                teacher_id,
            ),
        )
        conn.commit()
    return {"message": "Teaching plan updated", "id": plan_id}


@router.delete("/{plan_id}")
def delete_teaching_plan(
    plan_id: str,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        deleted = conn.execute(
            "DELETE FROM teaching_plans WHERE id = %s AND teacher_id = %s RETURNING id",
            (plan_id, teacher_id),
        ).fetchone()
        conn.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="Teaching plan not found")
    return {"message": "Teaching plan deleted"}


def _report_csv(plans: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Unit No",
            "Topic Code",
            "Sessions Required",
            "Topic",
            "Proposed Date",
            "Conduct Date",
            "Status",
            "Lecture Slot",
            "Course",
            "Section",
        ]
    )
    for plan in plans:
        writer.writerow(
            [
                plan["unit_no"],
                plan["topic_no"],
                plan["sessions_required"],
                plan["topic_description"],
                plan["proposed_date_label"],
                plan["conduct_date_label"],
                plan["status"],
                plan["lecture_slot"],
                f"{plan['course_code']} - {plan['course_name']}",
                plan["section"],
            ]
        )
    return buffer.getvalue()


@router.get("/report")
def teaching_plan_report(
    plan_type: str = "core",
    course_id: str | None = None,
    section: str | None = None,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    if plan_type not in PLAN_TYPES:
        raise HTTPException(status_code=400, detail="Invalid teaching plan tab")
    with get_connection() as conn:
        if course_id:
            _assert_course_owned(conn, course_id, teacher_id)
        plans = _list_plans(conn, teacher_id, plan_type, course_id, section or None)
    executed = sum(1 for plan in plans if plan["status"] == "Executed")
    return {
        "plans": plans,
        "summary": {
            "total": len(plans),
            "executed": executed,
            "planned": len(plans) - executed,
        },
    }


@router.get("/report.csv")
def teaching_plan_report_csv(
    plan_type: str = "core",
    course_id: str | None = None,
    section: str | None = None,
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    if plan_type not in PLAN_TYPES:
        raise HTTPException(status_code=400, detail="Invalid teaching plan tab")
    with get_connection() as conn:
        if course_id:
            _assert_course_owned(conn, course_id, teacher_id)
        plans = _list_plans(conn, teacher_id, plan_type, course_id, section or None)
    return Response(
        content=_report_csv(plans),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=PlannedVsExecuted.csv"},
    )


@router.get("/template.csv")
def teaching_plan_template(access_token: str | None = Cookie(default=None)):
    _require_teacher(access_token)
    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "course_code",
            "section",
            "unit_no",
            "topic_no",
            "topic_description",
            "sessions_required",
            "timetable_range",
            "proposed_date",
            "lecture_slot",
            "conduct_date",
        ]
    )
    writer.writerow(
        [
            "CS301",
            "S",
            "1",
            "1",
            "Unit 1 Introduction Time complexity",
            "2",
            "26/08/2026 - 09/12/2026",
            "2026-08-26",
            LECTURE_SLOTS[2],
            "",
        ]
    )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=TeachingPlanTemplate.csv"},
    )


@router.post("/upload")
async def upload_teaching_plan(
    plan_type: str = Form("core"),
    session_label: str = Form(...),
    college_label: str = Form(...),
    teaching_for: str = Form("Theory"),
    file: UploadFile = File(...),
    access_token: str | None = Cookie(default=None),
):
    ensure_teaching_plan_schema()
    _teacher, teacher_id = _require_teacher(access_token)
    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload the template saved as CSV")
    raw = await file.read()
    if len(raw) > 1_000_000:
        raise HTTPException(status_code=400, detail="File is too large")
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "course_code" not in reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header row does not match the template")

    created = 0
    errors: list[str] = []
    with get_connection() as conn:
        courses = _teacher_courses(conn, teacher_id)
        by_code = {c["course_code"]: c for c in courses}
        for index, row in enumerate(reader, start=2):
            code = (row.get("course_code") or "").strip()
            course = by_code.get(code)
            if not course:
                errors.append(f"Row {index}: course {code or '(blank)'} is not yours")
                continue
            if course["plan_type"] != plan_type:
                errors.append(f"Row {index}: {code} belongs on the other teaching plan tab")
                continue
            proposed_raw = (row.get("proposed_date") or "").strip()
            if not proposed_raw:
                errors.append(f"Row {index}: proposed_date is required")
                continue
            try:
                proposed = _parse_user_date(proposed_raw)
            except ValueError:
                errors.append(f"Row {index}: invalid proposed_date")
                continue
            weekday = proposed.strftime("%A").lower()
            if weekday not in WEEKDAYS:
                errors.append(f"Row {index}: proposed date falls on a Sunday")
                continue
            conduct_raw = (row.get("conduct_date") or "").strip()
            slot = (row.get("lecture_slot") or "").strip()
            try:
                payload = TeachingPlanIn(
                    plan_type=plan_type,
                    session_label=session_label,
                    college_label=college_label,
                    course_id=course["id"],
                    teaching_for=(row.get("teaching_for") or teaching_for or "Theory").strip()
                    or "Theory",
                    section=(row.get("section") or "S").strip() or "S",
                    unit_no=int(row.get("unit_no") or 0),
                    topic_no=int(row.get("topic_no") or 0),
                    topic_description=(row.get("topic_description") or "").strip(),
                    sessions_required=int(row.get("sessions_required") or 0),
                    timetable_range=(row.get("timetable_range") or "").strip(),
                    proposed_dates={weekday: proposed.isoformat()},
                    lecture_slots={weekday: slot} if slot else {},
                    conduct_date=conduct_raw or None,
                )
                normalized = _normalize_plan(payload, courses)
                title = _topic_title(
                    conn, normalized["course_id"], normalized["unit_no"], normalized["topic_no"]
                )
                if not normalized["topic_description"]:
                    normalized["topic_description"] = title
            except HTTPException as exc:
                errors.append(f"Row {index}: {exc.detail}")
                continue
            except (ValidationError, ValueError):
                errors.append(f"Row {index}: check unit, topic, and sessions required")
                continue

            conn.execute(
                """
                INSERT INTO teaching_plans (
                    teacher_id, course_id, plan_type, session_label, college_label,
                    teaching_for, section, unit_no, topic_no, topic_description,
                    sessions_required, timetable_range, proposed_date, conduct_date,
                    lecture_slot, proposed_dates, lecture_slots
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    teacher_id,
                    normalized["course_id"],
                    normalized["plan_type"],
                    normalized["session_label"],
                    normalized["college_label"],
                    normalized["teaching_for"],
                    normalized["section"],
                    normalized["unit_no"],
                    normalized["topic_no"],
                    normalized["topic_description"],
                    normalized["sessions_required"],
                    normalized["timetable_range"],
                    normalized["proposed_date"],
                    normalized["conduct_date"],
                    normalized["lecture_slot"],
                    Json(normalized["proposed_dates"]),
                    Json(normalized["lecture_slots"]),
                ),
            )
            created += 1
        conn.commit()

    if created == 0 and errors:
        raise HTTPException(status_code=400, detail="; ".join(errors[:5]))
    return {"message": f"Uploaded {created} row(s)", "created": created, "errors": errors[:8]}
