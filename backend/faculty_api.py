"""Faculty-facing API routes (teacher role)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/faculty", tags=["faculty"])
PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = PROJECT_ROOT / "database" / "assignments"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
ASSIGNMENT_EXTENSIONS = {".doc", ".docx", ".jpeg", ".jpg", ".pdf"}
ASSIGNMENT_MAX_BYTES = 10 * 1024 * 1024


def _verify_user(access_token: str | None) -> dict[str, Any]:
    # Lazy import to avoid circular import with main.py
    from main import verify_token

    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")
    return verify_token(access_token)


def _require_teacher(access_token: str | None) -> tuple[dict[str, Any], str]:
    user = _verify_user(access_token)
    if user["role"] != "teacher":
        raise HTTPException(status_code=403, detail="Only teachers can access this")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, full_name, employee_id, department FROM teachers WHERE user_id = %s",
            (user["id"],),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Teacher profile not found")

    teacher = {
        "id": str(row[0]),
        "full_name": row[1],
        "employee_id": row[2],
        "department": row[3],
        "email": user["email"],
        "role": user["role"],
    }
    return teacher, str(row[0])


def _assert_course_owned(conn, course_id: str, teacher_id: str) -> tuple:
    row = conn.execute(
        """
        SELECT id, course_code, course_name, semester, description
        FROM courses
        WHERE id = %s AND teacher_id = %s
        """,
        (course_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Course not found for this teacher")
    return row


class CreateAssignmentRequest(BaseModel):
    course_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    due_date: str
    max_marks: float = Field(default=20, gt=0)
    student_ids: list[str] | None = None  # reserved for future section targeting


class GradeSubmissionRequest(BaseModel):
    marks_obtained: float = Field(ge=0)
    feedback: str = ""
    display_to_student: bool = True
    checked: bool = True


def ensure_lms_schema(conn) -> None:
    conn.execute(
        "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS attachment_name TEXT"
    )
    conn.execute(
        "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS attachment_url TEXT"
    )
    conn.execute(
        "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS attachment_size_bytes BIGINT"
    )
    conn.execute(
        """
        ALTER TABLE submissions
        ADD COLUMN IF NOT EXISTS display_to_student BOOLEAN NOT NULL DEFAULT TRUE
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS assignment_recipients (
            assignment_id UUID NOT NULL
                REFERENCES assignments(id) ON DELETE CASCADE,
            student_id UUID NOT NULL
                REFERENCES students(id) ON DELETE CASCADE,
            PRIMARY KEY (assignment_id, student_id)
        )
        """
    )


def _program_for(course_code: str) -> str:
    if str(course_code).upper().startswith("EC"):
        return "B. Tech - ECE"
    return "B. Tech - CSE"


def _media_type(name: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(Path(name or "").suffix.lower(), "application/octet-stream")


@router.get("/me")
def faculty_me(access_token: str | None = Cookie(default=None)):
    teacher, _ = _require_teacher(access_token)
    return {"teacher": teacher}


@router.get("/dashboard")
def faculty_dashboard(access_token: str | None = Cookie(default=None)):
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        courses = conn.execute(
            """
            SELECT COUNT(*) FROM courses WHERE teacher_id = %s
            """,
            (teacher_id,),
        ).fetchone()[0]
        assignments = conn.execute(
            """
            SELECT COUNT(*) FROM assignments WHERE created_by = %s
            """,
            (teacher_id,),
        ).fetchone()[0]
        pending_checks = conn.execute(
            """
            SELECT COUNT(*)
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            WHERE a.created_by = %s AND s.marks_obtained IS NULL
            """,
            (teacher_id,),
        ).fetchone()[0]
        active_assignments = conn.execute(
            """
            SELECT COUNT(*)
            FROM assignments
            WHERE created_by = %s AND due_date > NOW()
            """,
            (teacher_id,),
        ).fetchone()[0]
        casual_leave_balance = 0.0
        upcoming_holidays = 2
        try:
            from establishment_api import ensure_establishment_schema, _seed_teacher, HOLIDAYS_2026
            from datetime import date as _date

            ensure_establishment_schema(conn)
            _seed_teacher(conn, teacher_id, teacher.get("department") or "Computer Science")
            bal = conn.execute(
                """
                SELECT opening_balance, credit, taken
                FROM leave_balances
                WHERE teacher_id = %s AND leave_name = 'Casual Leave'
                ORDER BY period_label DESC
                LIMIT 1
                """,
                (teacher_id,),
            ).fetchone()
            if bal:
                casual_leave_balance = float(bal[0] or 0) + float(bal[1] or 0) - float(bal[2] or 0)
            today = _date.today()
            upcoming_holidays = sum(1 for day, _name in HOLIDAYS_2026 if day >= today and day.month == today.month)
        except Exception:
            pass

    return {
        "teacher": teacher,
        "stats": {
            "courses": int(courses or 0),
            "assignments": int(assignments or 0),
            "pending_checks": int(pending_checks or 0),
            "active_assignments": int(active_assignments or 0),
            "biometric_attendance": 57.14,
            "casual_leave_balance": casual_leave_balance,
            "upcoming_holidays": upcoming_holidays,
        },
        "notices": [
            {
                "month": "Nov",
                "day": "04",
                "title": "3rd In Term Exam Form (Make-up) November-2026",
            },
            {
                "month": "Mar",
                "day": "22",
                "title": "Back Exam Form (Offline) Odd Sem 2026-27",
            },
            {
                "month": "Dec",
                "day": "15",
                "title": "Malpractice cases review notice",
            },
        ],
    }


@router.get("/courses")
def faculty_courses(
    semester: int | None = None,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)
    params: list[Any] = [teacher_id]
    semester_sql = ""
    if semester is not None:
        semester_sql = "AND c.semester = %s"
        params.append(semester)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.id, c.course_code, c.course_name, c.semester, c.description,
                   COUNT(DISTINCT e.student_id) AS student_count,
                   COUNT(DISTINCT a.id) AS assignment_count
            FROM courses c
            LEFT JOIN enrollments e ON e.course_id = c.id
            LEFT JOIN assignments a ON a.course_id = c.id
            WHERE c.teacher_id = %s
            {semester_sql}
            GROUP BY c.id
            ORDER BY c.semester, c.course_code
            """,
            params,
        ).fetchall()

    return {
        "courses": [
            {
                "id": str(r[0]),
                "course_code": r[1],
                "course_name": r[2],
                "semester": r[3],
                "year_semester": ROMAN.get(int(r[3]), str(r[3])),
                "program": _program_for(r[1]),
                "course_type": "Theory",
                "section": "S",
                "description": r[4] or "",
                "student_count": int(r[5] or 0),
                "assignment_count": int(r[6] or 0),
            }
            for r in rows
        ]
    }


@router.get("/courses/{course_id}/students")
def faculty_course_students(
    course_id: str,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        _assert_course_owned(conn, course_id, teacher_id)
        rows = conn.execute(
            """
            SELECT s.id, s.roll_number, s.full_name, s.branch, s.semester, u.email
            FROM enrollments e
            JOIN students s ON s.id = e.student_id
            JOIN users u ON u.id = s.user_id
            WHERE e.course_id = %s
            ORDER BY s.roll_number
            """,
            (course_id,),
        ).fetchall()

    return {
        "students": [
            {
                "id": str(r[0]),
                "roll_number": r[1],
                "full_name": r[2],
                "branch": r[3],
                "semester": r[4],
                "email": r[5],
                "section": "S",
                "mobile": "",
            }
            for r in rows
        ]
    }


@router.get("/courses/{course_id}/assignments")
def faculty_course_assignments(
    course_id: str,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)
    now = datetime.now(timezone.utc)

    with get_connection() as conn:
        ensure_lms_schema(conn)
        course = _assert_course_owned(conn, course_id, teacher_id)
        rows = conn.execute(
            """
            SELECT a.id, a.title, a.description, a.due_date, a.max_marks, a.created_at,
                   a.attachment_name, a.attachment_size_bytes,
                   COUNT(s.id) AS reply_count,
                   COUNT(s.id) FILTER (WHERE s.marks_obtained IS NOT NULL) AS checked_count
            FROM assignments a
            LEFT JOIN submissions s ON s.assignment_id = a.id
            WHERE a.course_id = %s AND a.created_by = %s
            GROUP BY a.id
            ORDER BY a.created_at DESC
            """,
            (course_id, teacher_id),
        ).fetchall()

    assignments = []
    for r in rows:
        due = r[3]
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        status = "Active" if due > now else "Expired"
        assignments.append(
            {
                "id": str(r[0]),
                "title": r[1],
                "description": r[2] or "",
                "due_date": r[3].isoformat(),
                "max_marks": float(r[4]),
                "created_at": r[5].isoformat(),
                "attachment_name": r[6],
                "attachment_size_bytes": int(r[7]) if r[7] else None,
                "reply_count": int(r[8] or 0),
                "checked_count": int(r[9] or 0),
                "status": status,
            }
        )

    return {
        "course": {
            "id": str(course[0]),
            "course_code": course[1],
            "course_name": course[2],
            "semester": course[3],
        },
        "assignments": assignments,
    }


@router.post("/assignments")
def create_assignment(
    data: CreateAssignmentRequest,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)
    title = data.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    raw_due = (data.due_date or "").strip()
    try:
        # Accept ISO with Z, offset, or naive local datetime-local values.
        normalized = raw_due.replace("Z", "+00:00")
        if "T" in normalized and len(normalized) == 16:
            # e.g. 2026-11-24T15:30 (from datetime-local)
            due = datetime.fromisoformat(normalized).replace(tzinfo=timezone.utc)
        else:
            due = datetime.fromisoformat(normalized)
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid due_date") from exc

    with get_connection() as conn:
        ensure_lms_schema(conn)
        _assert_course_owned(conn, data.course_id, teacher_id)
        row = conn.execute(
            """
            INSERT INTO assignments
                (course_id, created_by, title, description, due_date, max_marks)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, title, due_date, max_marks, created_at
            """,
            (
                data.course_id,
                teacher_id,
                title,
                data.description.strip(),
                due,
                data.max_marks,
            ),
        ).fetchone()
        for student_id in data.student_ids or []:
            conn.execute(
                """
                INSERT INTO assignment_recipients (assignment_id, student_id)
                SELECT %s, s.id
                FROM students s
                JOIN enrollments e ON e.student_id = s.id
                WHERE s.id = %s AND e.course_id = %s
                ON CONFLICT DO NOTHING
                """,
                (row[0], student_id, data.course_id),
            )
        conn.commit()

    return {
        "message": "Assignment created",
        "assignment": {
            "id": str(row[0]),
            "title": row[1],
            "due_date": row[2].isoformat(),
            "max_marks": float(row[3]),
            "created_at": row[4].isoformat(),
        },
    }


@router.get("/assignments/{assignment_id}/submissions")
def list_submissions(
    assignment_id: str,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)

    with get_connection() as conn:
        ensure_lms_schema(conn)
        assignment = conn.execute(
            """
            SELECT a.id, a.title, a.due_date, a.max_marks, a.description,
                   c.id, c.course_code, c.course_name, c.semester
            FROM assignments a
            JOIN courses c ON c.id = a.course_id
            WHERE a.id = %s AND a.created_by = %s
            """,
            (assignment_id, teacher_id),
        ).fetchone()
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found")

        rows = conn.execute(
            """
            SELECT s.id, st.roll_number, st.full_name, s.submitted_at, s.status,
                   s.marks_obtained, s.feedback, s.file_name, s.file_url, s.file_size_bytes,
                   s.display_to_student
            FROM submissions s
            JOIN students st ON st.id = s.student_id
            WHERE s.assignment_id = %s
            ORDER BY s.submitted_at DESC
            """,
            (assignment_id,),
        ).fetchall()

    return {
        "assignment": {
            "id": str(assignment[0]),
            "title": assignment[1],
            "due_date": assignment[2].isoformat(),
            "max_marks": float(assignment[3]),
            "description": assignment[4] or "",
            "course_id": str(assignment[5]),
            "course_code": assignment[6],
            "course_name": assignment[7],
            "semester": assignment[8],
        },
        "submissions": [
            {
                "id": str(r[0]),
                "roll_number": r[1],
                "student_name": r[2],
                "submitted_at": r[3].isoformat() if r[3] else None,
                "status": r[4],
                "marks_obtained": float(r[5]) if r[5] is not None else None,
                "feedback": r[6] or "",
                "file_name": r[7],
                "file_url": r[8],
                "file_size_bytes": r[9],
                "display_to_student": bool(r[10]) if r[10] is not None else True,
                "checked": r[5] is not None,
            }
            for r in rows
        ],
    }


@router.post("/submissions/{submission_id}/grade")
def grade_submission(
    submission_id: str,
    data: GradeSubmissionRequest,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)

    with get_connection() as conn:
        ensure_lms_schema(conn)
        row = conn.execute(
            """
            SELECT s.id, a.max_marks
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            WHERE s.id = %s AND a.created_by = %s
            """,
            (submission_id, teacher_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Submission not found")

        max_marks = float(row[1])
        if data.marks_obtained > max_marks:
            raise HTTPException(
                status_code=400,
                detail=f"Marks cannot exceed max marks ({max_marks})",
            )

        marks = data.marks_obtained if data.checked else None
        conn.execute(
            """
            UPDATE submissions
            SET marks_obtained = %s,
                feedback = %s,
                display_to_student = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (marks, data.feedback.strip(), data.display_to_student, submission_id),
        )
        conn.commit()

    return {
        "message": "Marks saved",
        "submission_id": submission_id,
        "marks_obtained": data.marks_obtained,
        "feedback": data.feedback.strip(),
        "display_to_student": data.display_to_student,
    }


@router.get("/submissions/{submission_id}/file")
def download_submission_file(
    submission_id: str,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.file_name, s.file_url
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            WHERE s.id = %s AND a.created_by = %s
            """,
            (submission_id, teacher_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Submission not found")

    file_name, file_url = row
    if not file_url or not str(file_url).startswith("database/assignments/"):
        raise HTTPException(status_code=404, detail="Stored file path is invalid")

    path = PROJECT_ROOT / file_url
    if not path.exists():
        raise HTTPException(status_code=404, detail="Uploaded file was not found on disk")

    return FileResponse(
        path,
        filename=file_name or path.name,
        media_type=_media_type(file_name or path.name),
        content_disposition_type="inline",
    )


@router.post("/assignments/{assignment_id}/attachment")
async def upload_assignment_attachment(
    assignment_id: str,
    file: UploadFile = File(...),
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)
    original = Path(file.filename or "assignment").name
    ext = Path(original).suffix.lower()
    if ext not in ASSIGNMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Allowed file extensions: .doc, .docx, .jpeg, .jpg, .pdf",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(content) > ASSIGNMENT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Max size is 10 MB")

    stored = f"{uuid.uuid4().hex}{ext}"
    (UPLOAD_DIR / stored).write_bytes(content)
    relative = f"database/assignments/{stored}"

    with get_connection() as conn:
        ensure_lms_schema(conn)
        row = conn.execute(
            """
            UPDATE assignments
            SET attachment_name = %s,
                attachment_url = %s,
                attachment_size_bytes = %s,
                updated_at = NOW()
            WHERE id = %s AND created_by = %s
            RETURNING id
            """,
            (original, relative, len(content), assignment_id, teacher_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Assignment not found")
        conn.commit()

    return {
        "message": "File attached",
        "file_name": original,
        "file_size_bytes": len(content),
    }


@router.get("/assignments/{assignment_id}/attachment")
def download_assignment_attachment(
    assignment_id: str,
    access_token: str | None = Cookie(default=None),
):
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_lms_schema(conn)
        row = conn.execute(
            """
            SELECT attachment_name, attachment_url
            FROM assignments
            WHERE id = %s AND created_by = %s
            """,
            (assignment_id, teacher_id),
        ).fetchone()
    if not row or not row[1] or not str(row[1]).startswith("database/assignments/"):
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = PROJECT_ROOT / row[1]
    if not path.exists():
        raise HTTPException(status_code=404, detail="Attachment file was not found on disk")
    return FileResponse(
        path,
        filename=row[0] or path.name,
        media_type=_media_type(row[0] or path.name),
        content_disposition_type="inline",
    )
