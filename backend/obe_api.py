"""Faculty OBE: question-wise marks, exam paper, and mark entry report."""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from database import get_connection
from fastapi import APIRouter, Cookie, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from faculty_api import _assert_course_owned, _require_teacher

router = APIRouter(prefix="/api/faculty/obe", tags=["obe"])

SESSION_KEY = "2026-27-ODD"
QW_SESSION = "July-Dec 2026-2027 - SET"
REPORT_SESSION = "July-Dec 2026-2027 - School of Engineering and Technology"
SESSIONS = {
    QW_SESSION: SESSION_KEY,
    REPORT_SESSION: SESSION_KEY,
}
ADMISSION_BATCH = "2023-24"
STATUS_CODES = ("902", "903", "904")
SEEDED_EXAM = "II-IN TERM-NEW"
LOCK_MESSAGE = "You won't be able to modify the MARKS after it's been locked"
REPORT_EXAMS = ("INTERNAL EXAM", "ASSIGNMENT", "END TERM", SEEDED_EXAM)
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

# Question code, max marks. Totals match the recording (II-IN TERM-NEW = 40).
PAPERS: dict[str, list[tuple[str, float]]] = {
    "II-IN TERM-NEW": [
        ("A-1", 2.0),
        ("A-2", 2.0),
        ("B-1", 7.0),
        ("B-2", 7.0),
        ("C-1", 11.0),
        ("C-2", 11.0),
    ],
    "INTERNAL EXAM": [("A-1", 5.0), ("A-2", 5.0), ("B-1", 10.0), ("B-2", 10.0)],
    "ASSIGNMENT": [("A-1", 5.0), ("A-2", 5.0)],
    "END TERM": [
        ("A-1", 10.0),
        ("A-2", 10.0),
        ("B-1", 15.0),
        ("B-2", 15.0),
        ("C-1", 10.0),
        ("C-2", 10.0),
    ],
}
INTERNAL_EXAMS = ("II-IN TERM-NEW", "INTERNAL EXAM", "ASSIGNMENT")
EXTERNAL_EXAMS = ("END TERM",)
PROMPTS = {
    "A-1": "Define the core terms and state where they are used in {course}.",
    "A-2": "Differentiate the two approaches discussed for {course}.",
    "B-1": "Apply the standard method to the given short problem in {course}.",
    "B-2": "Solve the numerical or design problem for {course}.",
    "C-1": "Analyze the given case and justify the result for {course}.",
    "C-2": "Design or evaluate a solution for the {course} scenario.",
}

_schema_ready = False


class QuestionRowIn(BaseModel):
    student_id: str
    status_code: str | None = None
    marks: dict[str, float | None] = Field(default_factory=dict)


class SaveObeIn(BaseModel):
    session_label: str
    scheme: str
    exam_name: str
    course_id: str
    rows: list[QuestionRowIn] = Field(default_factory=list)


def scheme_for(department: str) -> str:
    dept = department or "Computer Science"
    return (
        f"Bachelor of Technology-{dept}-2023-24- Department of {dept} Engineering-New"
    )


def _course_type(course_name: str) -> str:
    name = (course_name or "").lower()
    if "lab" in name or "practical" in name:
        return "Practical"
    return "Theory"


def _section_for(course_name: str) -> str:
    return "DE1" if _course_type(course_name) == "Practical" else "DE"


def _co(code: str) -> str:
    return {"A": "CO1", "B": "CO2", "C": "CO3"}.get(code.split("-")[0], "CO1")


def _questions(exam_name: str, course_name: str) -> list[dict[str, Any]]:
    paper = PAPERS.get(exam_name) or []
    rows = []
    for code, maximum in paper:
        rows.append(
            {
                "code": code,
                "max_marks": maximum,
                "co": _co(code),
                "text": PROMPTS.get(code, code).format(course=course_name),
            }
        )
    return rows


def _max_marks(exam_name: str) -> float:
    return round(sum(maximum for _code, maximum in PAPERS.get(exam_name, [])), 2)


def ensure_obe_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS obe_sheets (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                course_id UUID NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
                session_key VARCHAR(40) NOT NULL,
                exam_name VARCHAR(40) NOT NULL,
                section VARCHAR(10) NOT NULL,
                locked BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (teacher_id, course_id, session_key, exam_name, section)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS obe_mark_rows (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                sheet_id UUID NOT NULL REFERENCES obe_sheets(id) ON DELETE CASCADE,
                student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                status_code VARCHAR(10),
                marks JSONB NOT NULL DEFAULT '{}'::jsonb,
                UNIQUE (sheet_id, student_id)
            )
            """
        )
        conn.commit()
    _schema_ready = True


def _session_key(label: str) -> str:
    key = SESSIONS.get(label)
    if not key:
        raise HTTPException(status_code=400, detail="Select a session")
    return key


def _check_scheme(teacher: dict[str, Any], scheme: str) -> str:
    expected = scheme_for(teacher.get("department") or "")
    if scheme != expected:
        raise HTTPException(status_code=400, detail="Select an assessment scheme")
    return expected


def _check_exam(exam_name: str) -> None:
    if exam_name not in PAPERS:
        raise HTTPException(status_code=400, detail="Select an exam name")


def _students(conn, course_id: str) -> list[tuple]:
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


def _sheet_row(conn, teacher_id: str, course_id: str, session_key: str, exam_name: str, section: str):
    return conn.execute(
        """
        SELECT id, locked
        FROM obe_sheets
        WHERE teacher_id = %s AND course_id = %s
          AND session_key = %s AND exam_name = %s AND section = %s
        """,
        (teacher_id, course_id, session_key, exam_name, section),
    ).fetchone()


def _seed_marks(index: int) -> tuple[str | None, dict[str, float]]:
    questions = PAPERS[SEEDED_EXAM]
    status = None
    if index % 6 == 4:
        status = "902"
    elif index % 8 == 6:
        status = "903"
    marks: dict[str, float] = {}
    ladder = (1, 2, 0, 1, 2, 3, 5, 7, 8, 9, 11)
    for q_index, (code, maximum) in enumerate(questions):
        if status:
            marks[code] = 0.0
        else:
            step = ladder[(index + q_index) % len(ladder)]
            marks[code] = float(min(maximum, step))
    return status, marks


def _seed_default(conn, teacher_id: str, course_id: str, session_key: str, section: str) -> None:
    created = conn.execute(
        """
        INSERT INTO obe_sheets (teacher_id, course_id, session_key, exam_name, section)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (teacher_id, course_id, session_key, exam_name, section) DO NOTHING
        RETURNING id
        """,
        (teacher_id, course_id, session_key, SEEDED_EXAM, section),
    ).fetchone()
    if not created:
        return
    for index, student in enumerate(_students(conn, course_id)):
        status, marks = _seed_marks(index)
        conn.execute(
            """
            INSERT INTO obe_mark_rows (sheet_id, student_id, status_code, marks)
            VALUES (%s, %s, %s, %s)
            """,
            (created[0], student[0], status, Json(marks)),
        )


def _component(conn, teacher_id: str, course_id: str, session_key: str, section: str, exams: tuple[str, ...]) -> str:
    if not exams:
        return "PENDING"
    locked = 0
    for exam_name in exams:
        row = _sheet_row(conn, teacher_id, course_id, session_key, exam_name, section)
        if row and row[1]:
            locked += 1
    return "DONE" if locked == len(exams) else "PENDING"


def _display_total(status: str | None, marks: dict[str, Any], exam_name: str) -> float | None:
    if status in STATUS_CODES:
        return float(status)
    if not marks:
        return None
    total = 0.0
    seen = False
    for code, _maximum in PAPERS[exam_name]:
        value = marks.get(code)
        if value is None or value == "":
            continue
        total += float(value)
        seen = True
    return round(total, 2) if seen else None


def _course_label(course, section: str) -> str:
    return f"{course[2]} - {course[1]} (Sec - {section})"


def _report_subject(course, section: str, department: str) -> str:
    semester = ROMAN.get(course[3], str(course[3]))
    name = (course[2] or "").upper()
    branch = department or "Computer Science"
    return (
        f"{course[1]}-{name}(SEM:{semester}- SECTION:{section}- "
        f"BRANCH:{branch}- ADMISSION BATCH:{ADMISSION_BATCH})"
    )


def _load_saved(conn, sheet_id) -> dict[str, tuple]:
    rows = conn.execute(
        """
        SELECT student_id::text, status_code, marks
        FROM obe_mark_rows
        WHERE sheet_id = %s
        """,
        (sheet_id,),
    ).fetchall()
    return {row[0]: row for row in rows}


def _sheet_payload(conn, teacher: dict[str, Any], teacher_id: str, course, session_label: str, exam_name: str) -> dict[str, Any]:
    section = _section_for(course[2])
    session_key = _session_key(session_label)
    students = _students(conn, course[0])
    sheet = _sheet_row(conn, teacher_id, str(course[0]), session_key, exam_name, section)
    saved = _load_saved(conn, sheet[0]) if sheet else {}
    locked = bool(sheet[1]) if sheet else False
    questions = _questions(exam_name, course[2])
    payload_rows = []
    for student in students:
        stored = saved.get(student[0])
        status = stored[1] if stored else None
        raw_marks = stored[2] if stored and isinstance(stored[2], dict) else {}
        marks = {}
        for question in questions:
            value = raw_marks.get(question["code"])
            marks[question["code"]] = None if value is None else float(value)
        if status in STATUS_CODES:
            marks = {question["code"]: 0.0 for question in questions}
        payload_rows.append(
            {
                "student_id": student[0],
                "roll_number": student[1],
                "name": student[2],
                "status_code": status if status in STATUS_CODES else None,
                "marks": marks,
                "total": _display_total(status, marks, exam_name),
            }
        )
    return {
        "session_label": session_label,
        "exam_name": exam_name,
        "max_marks": _max_marks(exam_name),
        "locked": locked,
        "lock_message": LOCK_MESSAGE,
        "student_count": len(payload_rows),
        "questions": questions,
        "course": {
            "id": str(course[0]),
            "code": course[1],
            "name": course[2],
            "semester": course[3],
            "semester_label": ROMAN.get(course[3], str(course[3])),
            "course_type": _course_type(course[2]),
            "section": section,
            "label": _course_label(course, section),
        },
        "rows": payload_rows,
        "faculty_id": teacher.get("employee_id") or "",
    }


def _normalize_row(row: QuestionRowIn, exam_name: str) -> tuple[str | None, dict[str, float]]:
    questions = PAPERS[exam_name]
    status = (row.status_code or "").strip()
    if status:
        if status not in STATUS_CODES:
            raise HTTPException(status_code=400, detail="Status code must be 902, 903, or 904")
        return status, {code: 0.0 for code, _maximum in questions}

    cleaned: dict[str, float] = {}
    for code, maximum in questions:
        if code not in row.marks or row.marks[code] is None:
            continue
        value = float(row.marks[code])
        if value < 0 or value > maximum:
            raise HTTPException(
                status_code=400,
                detail=f"{code} marks must be between 0 and {maximum:.2f}",
            )
        cleaned[code] = round(value, 2)
    return None, cleaned


def _row_complete(status: str | None, marks: dict[str, float], exam_name: str) -> bool:
    if status in STATUS_CODES:
        return True
    return all(code in marks for code, _maximum in PAPERS[exam_name])


def _upsert_sheet(conn, teacher_id: str, course_id: str, session_key: str, exam_name: str, section: str, lock: bool):
    existing = _sheet_row(conn, teacher_id, course_id, session_key, exam_name, section)
    if existing and existing[1]:
        raise HTTPException(status_code=400, detail=LOCK_MESSAGE)
    if existing:
        conn.execute(
            """
            UPDATE obe_sheets
            SET locked = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (lock, existing[0]),
        )
        return existing[0]
    created = conn.execute(
        """
        INSERT INTO obe_sheets (teacher_id, course_id, session_key, exam_name, section, locked)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (teacher_id, course_id, session_key, exam_name, section, lock),
    ).fetchone()
    return created[0]


def _write_rows(conn, sheet_id, cleaned: dict[str, tuple[str | None, dict[str, float]]]) -> None:
    for student_id, (status, marks) in cleaned.items():
        conn.execute(
            """
            INSERT INTO obe_mark_rows (sheet_id, student_id, status_code, marks)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (sheet_id, student_id)
            DO UPDATE SET status_code = EXCLUDED.status_code, marks = EXCLUDED.marks
            """,
            (sheet_id, student_id, status, Json(marks)),
        )


def _save(body: SaveObeIn, teacher: dict[str, Any], teacher_id: str, lock: bool) -> dict[str, Any]:
    ensure_obe_schema()
    _check_scheme(teacher, body.scheme)
    _check_exam(body.exam_name)
    session_key = _session_key(body.session_label)

    with get_connection() as conn:
        course = _assert_course_owned(conn, body.course_id, teacher_id)
        section = _section_for(course[2])
        _seed_default(conn, teacher_id, str(course[0]), session_key, section)
        students = _students(conn, str(course[0]))
        if not students:
            raise HTTPException(status_code=400, detail="No students in this subject")
        allowed = {row[0] for row in students}
        cleaned: dict[str, tuple[str | None, dict[str, float]]] = {}
        for row in body.rows:
            if row.student_id not in allowed:
                raise HTTPException(status_code=400, detail="A student on this sheet is not enrolled")
            cleaned[row.student_id] = _normalize_row(row, body.exam_name)
        if lock:
            missing = [
                roll
                for sid, roll, _name in students
                if not _row_complete(*(cleaned.get(sid) or (None, {})), body.exam_name)
            ]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="Enter marks or a status code for every question and student before locking",
                )
        sheet_id = _upsert_sheet(
            conn, teacher_id, str(course[0]), session_key, body.exam_name, section, lock
        )
        _write_rows(conn, sheet_id, cleaned)
        conn.commit()
        payload = _sheet_payload(conn, teacher, teacher_id, course, body.session_label, body.exam_name)

    payload["ok"] = True
    payload["message"] = "Marks locked." if lock else "Marks saved."
    return payload


def _xml(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _col_letter(index: int) -> str:
    letters = ""
    number = index
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _build_xlsx(rows: list[list[str]]) -> bytes:
    sheet_rows = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row, start=1):
            ref = f"{_col_letter(c_index)}{r_index}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{_xml(value)}</t></is></c>'
            )
        sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Question Wise Marks" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def _cell_text(cell, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//m:t", NS))
    value = cell.find("m:v", NS)
    if value is None or value.text is None:
        return ""
    if kind == "s":
        index = int(value.text)
        return shared[index] if 0 <= index < len(shared) else ""
    return value.text


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch.upper()) - 64)
    return index


def _read_xlsx(data: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="This file is not an .xlsx workbook. Save it as .xlsx and import again.",
        ) from exc
    shared: list[str] = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root.findall("m:si", NS):
            shared.append("".join(node.text or "" for node in item.findall(".//m:t", NS)))
    sheet_name = "xl/worksheets/sheet1.xml"
    if sheet_name not in archive.namelist():
        sheets = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")]
        if not sheets:
            raise HTTPException(status_code=400, detail="The workbook has no worksheet")
        sheet_name = sheets[0]
    root = ET.fromstring(archive.read(sheet_name))
    table: list[list[str]] = []
    for row in root.findall("m:sheetData/m:row", NS):
        values: list[str] = []
        for cell in row.findall("m:c", NS):
            index = _col_index(cell.attrib.get("r", ""))
            while len(values) < max(index - 1, 0):
                values.append("")
            if index:
                if len(values) == index - 1:
                    values.append(_cell_text(cell, shared))
                else:
                    values[index - 1] = _cell_text(cell, shared)
            else:
                values.append(_cell_text(cell, shared))
        table.append(values)
    return table


def _read_upload(filename: str, data: bytes) -> list[list[str]]:
    lower = (filename or "").lower()
    if lower.endswith(".csv"):
        text = data.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]
    if lower.endswith(".xls") and not lower.endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail="Save the file as .xlsx (Excel Workbook) and import again.",
        )
    return _read_xlsx(data)


def _sheet_table(payload: dict[str, Any]) -> list[list[str]]:
    questions = payload["questions"]
    header = ["Reg No. / Roll No.", "Student Name", "Status Code"]
    header.extend(question["code"] for question in questions)
    header.append("Total Marks")
    rows = [header]
    for row in payload["rows"]:
        status = row.get("status_code") or ""
        values = [row["roll_number"], row["name"], status]
        for question in questions:
            mark = row["marks"].get(question["code"])
            values.append("" if mark is None else f"{float(mark):.2f}")
        total = row.get("total")
        values.append("" if total is None else f"{float(total):.2f}")
        rows.append(values)
    return rows


def _header_index(header: list[str], *needles: str) -> int | None:
    for index, cell in enumerate(header):
        label = cell.strip().lower()
        if any(needle in label for needle in needles):
            return index
    return None


def _parse_import(table: list[list[str]], exam_name: str, roll_to_id: dict[str, str]) -> dict[str, tuple[str | None, dict[str, float]]]:
    if not table:
        raise HTTPException(status_code=400, detail="The Excel file is empty")
    header = [cell.strip() for cell in table[0]]
    folded = [cell.lower() for cell in header]
    roll_index = _header_index(folded, "roll", "reg")
    if roll_index is None:
        raise HTTPException(status_code=400, detail="The file needs a Reg No. / Roll No. column")
    status_index = _header_index(folded, "status")
    question_index: dict[str, int] = {}
    for code, _maximum in PAPERS[exam_name]:
        for index, label in enumerate(folded):
            if label == code.lower() or label.startswith(code.lower()):
                question_index[code] = index
                break
    cleaned: dict[str, tuple[str | None, dict[str, float]]] = {}
    for raw in table[1:]:
        if roll_index >= len(raw):
            continue
        roll = raw[roll_index].strip()
        student_id = roll_to_id.get(roll)
        if not student_id:
            continue
        status = ""
        if status_index is not None and status_index < len(raw):
            status = raw[status_index].strip()
            if status.endswith(".00"):
                status = status[:-3]
            if status and status not in STATUS_CODES:
                raise HTTPException(status_code=400, detail=f"Unknown status code {status} for {roll}")
        marks: dict[str, float] = {}
        for code, maximum in PAPERS[exam_name]:
            index = question_index.get(code)
            text = raw[index].strip() if index is not None and index < len(raw) else ""
            if not text:
                continue
            token = text[:-3] if text.endswith(".00") else text
            if token in STATUS_CODES:
                status = token
                continue
            try:
                value = float(text)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"{code} for {roll} is not a number",
                ) from exc
            if value < 0 or value > maximum:
                raise HTTPException(
                    status_code=400,
                    detail=f"{code} for {roll} must be between 0 and {maximum:.2f}",
                )
            marks[code] = round(value, 2)
        if status:
            marks = {code: 0.0 for code, _maximum in PAPERS[exam_name]}
        cleaned[student_id] = (status or None, marks)
    if not cleaned:
        raise HTTPException(status_code=400, detail="No matching roll numbers in this file")
    return cleaned


@router.get("/meta")
def obe_meta(access_token: str | None = Cookie(default=None)) -> dict[str, Any]:
    ensure_obe_schema()
    teacher, _teacher_id = _require_teacher(access_token)
    scheme = scheme_for(teacher.get("department") or "")
    exams = []
    for name in ("II-IN TERM-NEW", "INTERNAL EXAM", "ASSIGNMENT", "END TERM"):
        exams.append(
            {
                "name": name,
                "max_marks": _max_marks(name),
                "questions": _questions(name, "the subject"),
            }
        )
    return {
        "question_sessions": [QW_SESSION],
        "report_sessions": [REPORT_SESSION],
        "schemes": [scheme],
        "subject_types": ["Theory", "Practical"],
        "exams": exams,
        "status_codes": [
            {"code": "902", "label": "Absent"},
            {"code": "903", "label": "UFM"},
            {"code": "904", "label": "Detained"},
        ],
        "lock_message": LOCK_MESSAGE,
        "faculty_id": teacher.get("employee_id") or "",
        "department": teacher.get("department") or "",
    }


@router.get("/subjects")
def obe_subjects(
    session_label: str = Query(...),
    scheme: str = Query(...),
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    ensure_obe_schema()
    teacher, teacher_id = _require_teacher(access_token)
    scheme_name = _check_scheme(teacher, scheme)
    session_key = _session_key(session_label)
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
        subjects = []
        for index, course in enumerate(courses, start=1):
            section = _section_for(course[2])
            _seed_default(conn, teacher_id, course[0], session_key, section)
            subjects.append(
                {
                    "sr": index,
                    "course_id": course[0],
                    "code": course[1],
                    "name": course[2],
                    "label": f"{course[2]} - {course[1]}",
                    "scheme": scheme_name,
                    "semester": ROMAN.get(course[3], str(course[3])),
                    "section": section,
                    "batch": section,
                    "internal": _component(
                        conn, teacher_id, course[0], session_key, section, INTERNAL_EXAMS
                    ),
                    "external": _component(
                        conn, teacher_id, course[0], session_key, section, EXTERNAL_EXAMS
                    ),
                    "faculty_id": teacher.get("employee_id") or "",
                    "course_type": _course_type(course[2]),
                }
            )
        conn.commit()
    return {
        "session_label": session_label,
        "scheme": scheme_name,
        "subjects": subjects,
    }


@router.get("/sheet")
def obe_sheet(
    session_label: str = Query(...),
    scheme: str = Query(...),
    exam_name: str = Query(...),
    course_id: str = Query(...),
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    ensure_obe_schema()
    teacher, teacher_id = _require_teacher(access_token)
    _check_scheme(teacher, scheme)
    _check_exam(exam_name)
    session_key = _session_key(session_label)
    with get_connection() as conn:
        course = _assert_course_owned(conn, course_id, teacher_id)
        _seed_default(conn, teacher_id, str(course[0]), session_key, _section_for(course[2]))
        conn.commit()
        return _sheet_payload(conn, teacher, teacher_id, course, session_label, exam_name)


@router.post("/save")
def save_obe(body: SaveObeIn, access_token: str | None = Cookie(default=None)) -> dict[str, Any]:
    teacher, teacher_id = _require_teacher(access_token)
    return _save(body, teacher, teacher_id, lock=False)


@router.post("/lock")
def lock_obe(body: SaveObeIn, access_token: str | None = Cookie(default=None)) -> dict[str, Any]:
    teacher, teacher_id = _require_teacher(access_token)
    return _save(body, teacher, teacher_id, lock=True)


@router.get("/excel")
def download_excel(
    session_label: str = Query(...),
    scheme: str = Query(...),
    exam_name: str = Query(...),
    course_id: str = Query(...),
    access_token: str | None = Cookie(default=None),
) -> Response:
    payload = obe_sheet(session_label, scheme, exam_name, course_id, access_token)
    data = _build_xlsx(_sheet_table(payload))
    filename = f"question-wise-{payload['course']['code']}-{exam_name}.xlsx".replace(" ", "-")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_excel(
    session_label: str = Query(...),
    scheme: str = Query(...),
    exam_name: str = Query(...),
    course_id: str = Query(...),
    file: UploadFile = File(...),
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    ensure_obe_schema()
    teacher, teacher_id = _require_teacher(access_token)
    _check_scheme(teacher, scheme)
    _check_exam(exam_name)
    session_key = _session_key(session_label)
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is larger than 2 MB")
    table = _read_upload(file.filename or "", data)

    with get_connection() as conn:
        course = _assert_course_owned(conn, course_id, teacher_id)
        section = _section_for(course[2])
        _seed_default(conn, teacher_id, str(course[0]), session_key, section)
        existing = _sheet_row(conn, teacher_id, str(course[0]), session_key, exam_name, section)
        if existing and existing[1]:
            raise HTTPException(status_code=400, detail=LOCK_MESSAGE)
        students = _students(conn, str(course[0]))
        cleaned = _parse_import(table, exam_name, {row[1]: row[0] for row in students})
        sheet_id = _upsert_sheet(
            conn, teacher_id, str(course[0]), session_key, exam_name, section, False
        )
        _write_rows(conn, sheet_id, cleaned)
        conn.commit()
        payload = _sheet_payload(conn, teacher, teacher_id, course, session_label, exam_name)
    payload["ok"] = True
    payload["message"] = f"Imported marks for {len(cleaned)} students."
    return payload


@router.get("/report")
def mark_report(
    session_label: str = Query(...),
    subject_type: str = Query(...),
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    ensure_obe_schema()
    teacher, teacher_id = _require_teacher(access_token)
    if session_label not in SESSIONS:
        raise HTTPException(status_code=400, detail="Select a session")
    if subject_type not in ("Theory", "Practical"):
        raise HTTPException(status_code=400, detail="Select a subject type")
    session_key = SESSIONS[session_label]
    department = teacher.get("department") or ""
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
        rows = []
        serial = 1
        for course in courses:
            if _course_type(course[2]) != subject_type:
                continue
            section = _section_for(course[2])
            _seed_default(conn, teacher_id, course[0], session_key, section)
            for exam_name in REPORT_EXAMS:
                rows.append(
                    {
                        "sr": serial,
                        "course_id": course[0],
                        "subject": _report_subject(course, section, department),
                        "semester": ROMAN.get(course[3], str(course[3])),
                        "section": section,
                        "exam_name": exam_name,
                        "course_type": subject_type,
                    }
                )
                serial += 1
        conn.commit()
    return {"session_label": session_label, "subject_type": subject_type, "rows": rows}


@router.get("/paper")
def exam_paper(
    session_label: str = Query(...),
    scheme: str = Query(...),
    exam_name: str = Query(...),
    course_id: str = Query(...),
    access_token: str | None = Cookie(default=None),
) -> dict[str, Any]:
    ensure_obe_schema()
    teacher, teacher_id = _require_teacher(access_token)
    scheme_name = _check_scheme(teacher, scheme)
    _check_exam(exam_name)
    _session_key(session_label)
    with get_connection() as conn:
        course = _assert_course_owned(conn, course_id, teacher_id)
    section = _section_for(course[2])
    questions = _questions(exam_name, course[2])
    return {
        "session_label": session_label,
        "scheme": scheme_name,
        "exam_name": exam_name,
        "max_marks": _max_marks(exam_name),
        "course": {
            "id": str(course[0]),
            "code": course[1],
            "name": course[2],
            "semester_label": ROMAN.get(course[3], str(course[3])),
            "section": section,
            "label": _course_label(course, section),
        },
        "questions": questions,
    }
