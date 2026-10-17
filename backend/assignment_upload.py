"""
Assignment upload API for ERP_AI.

Integrate into main.py:
    from assignment_upload import router as assignment_upload_router
    app.include_router(assignment_upload_router)

This module expects main.py's project layout:
    ERP_AI/
      backend/...
      database/assignments/
      frontend/LMS/

Set ERP_PROJECT_ROOT in the environment only if your repository layout differs.
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from database import get_connection
from fastapi import APIRouter, Cookie, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

router = APIRouter()

# Robust across machines: resolve from this file's location, not a user's home path.
# Expected layout is ERP_AI/backend/assignment_upload.py.
PROJECT_ROOT = Path(
    os.getenv("ERP_PROJECT_ROOT", str(Path(__file__).resolve().parent.parent))
).resolve()
UPLOAD_DIR = PROJECT_ROOT / "database" / "assignments"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = 3 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_MIME_TYPES = {
    ".pdf": {"application/pdf", "application/x-pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
        "application/zip",
    },
}


def _verify_student(access_token: str | None):
    # Import the same verifier used by main.py, avoiding duplicate JWT logic.
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    # Import at request time to avoid circular imports at module import.
    from main import verify_token

    user = verify_token(access_token)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Only students can upload assignments")
    return user


def _student_id(conn, user_id: str):
    row = conn.execute(
        "SELECT id FROM students WHERE user_id = %s",
        (user_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Student profile not found")
    return row[0]


def _get_assignment_for_enrolled_student(conn, assignment_id: str, student_id):
    row = conn.execute(
        """
        SELECT a.id, a.course_id, a.title, a.due_date,
               s.id, s.file_name, s.file_url, s.file_size_bytes, s.submitted_at
        FROM assignments a
        JOIN enrollments e
          ON e.course_id = a.course_id
         AND e.student_id = %s
        LEFT JOIN submissions s
          ON s.assignment_id = a.id
         AND s.student_id = e.student_id
        WHERE a.id = %s
        FOR UPDATE OF a
        """,
        (student_id, assignment_id),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Assignment not found or you are not enrolled in its course",
        )
    return row


@router.get("/api/assignments/{assignment_id}")
def get_assignment_upload_details(
    assignment_id: str,
    access_token: str | None = Cookie(default=None),
):
    user = _verify_student(access_token)
    with get_connection() as conn:
        student_id = _student_id(conn, user["id"])
        row = _get_assignment_for_enrolled_student(conn, assignment_id, student_id)

    assignment_id_db, course_id, title, due_date, submission_id, file_name, file_url, size, submitted_at = row
    now = datetime.now(timezone.utc)
    return {
        "id": str(assignment_id_db),
        "course_id": str(course_id),
        "title": title,
        "due_date": due_date.isoformat(),
        "submitted": submission_id is not None,
        "file_name": file_name,
        "file_size_bytes": size,
        "submitted_at": submitted_at.isoformat() if submitted_at else None,
        "can_replace": submission_id is not None and now <= due_date,
        # Initial uploads are allowed for this demo even if the due date passed.
        "can_upload": True,
    }


@router.post("/api/assignments/{assignment_id}/upload")
async def upload_assignment_file(
    assignment_id: str,
    file: UploadFile = File(...),
    access_token: str | None = Cookie(default=None),
):
    user = _verify_student(access_token)

    original_name = Path(file.filename or "").name
    extension = Path(original_name).suffix.lower()
    if not original_name or extension not in ALLOWED_EXTENSIONS:
        await file.close()
        raise HTTPException(status_code=400, detail="Only PDF and DOCX files are allowed")

    # Read at most one byte beyond the limit so oversized files cannot bypass checks.
    content = await file.read(MAX_FILE_BYTES + 1)
    await file.close()

    if not content:
        raise HTTPException(status_code=400, detail="The selected file is empty")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 3 MB limit")

    # Basic signature checks prevent relying on the extension alone.
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="The file does not appear to be a valid PDF")
    if extension == ".docx" and not content.startswith(b"PK"):
        raise HTTPException(status_code=400, detail="The file does not appear to be a valid DOCX")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{extension}"
    destination = UPLOAD_DIR / stored_name
    relative_file_url = f"database/assignments/{stored_name}"

    old_stored_path = None
    wrote_new_file = False

    try:
        with get_connection() as conn:
            student_id = _student_id(conn, user["id"])
            row = _get_assignment_for_enrolled_student(conn, assignment_id, student_id)

            assignment_id_db, _course_id, _title, due_date, submission_id, _old_name, old_file_url, _old_size, _submitted_at = row
            now = datetime.now(timezone.utc)

            # Replacement is allowed only until the assignment due date.
            # Initial uploads remain allowed in this demo, including after due date.
            if submission_id is not None and now > due_date:
                raise HTTPException(
                    status_code=409,
                    detail="The due date has passed. You can no longer replace your submitted file.",
                )

            # Write new file before DB update. If anything fails, remove this new file.
            with destination.open("xb") as output:
                output.write(content)
            wrote_new_file = True

            status = "submitted" if now <= due_date else "late"
            if submission_id is None:
                conn.execute(
                    """
                    INSERT INTO submissions
                        (assignment_id, student_id, file_name, file_url,
                         file_size_bytes, submitted_at, updated_at, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        assignment_id_db, student_id, original_name,
                        relative_file_url, len(content), now, now, status,
                    ),
                )
                replaced = False
            else:
                old_file_url = old_file_url or ""
                if old_file_url.startswith("database/assignments/"):
                    old_stored_path = PROJECT_ROOT / old_file_url
                conn.execute(
                    """
                    UPDATE submissions
                    SET file_name = %s,
                        file_url = %s,
                        file_size_bytes = %s,
                        submitted_at = %s,
                        updated_at = %s,
                        status = %s
                    WHERE assignment_id = %s AND student_id = %s
                    """,
                    (
                        original_name, relative_file_url, len(content),
                        now, now, status, assignment_id_db, student_id,
                    ),
                )
                replaced = True

        # DB transaction committed. Remove old managed file only after commit.
        if replaced and old_stored_path:
            try:
                resolved_old = old_stored_path.resolve()
                if resolved_old.parent == UPLOAD_DIR.resolve() and resolved_old != destination.resolve():
                    resolved_old.unlink(missing_ok=True)
            except OSError:
                # Orphan cleanup is non-fatal; submission now points to the new file.
                pass

        return {
            "message": "File replaced successfully" if replaced else "Assignment submitted successfully",
            "file_name": original_name,
            "file_size_bytes": len(content),
            "submitted_at": now.isoformat(),
            "status": status,
        }

    except HTTPException:
        if wrote_new_file:
            destination.unlink(missing_ok=True)
        raise
    except Exception:
        if wrote_new_file:
            destination.unlink(missing_ok=True)
        raise


@router.get("/api/submissions/{assignment_id}/file")
def download_own_submission(
    assignment_id: str,
    access_token: str | None = Cookie(default=None),
):
    user = _verify_student(access_token)
    with get_connection() as conn:
        student_id = _student_id(conn, user["id"])
        row = conn.execute(
            """
            SELECT s.file_name, s.file_url
            FROM submissions s
            JOIN assignments a ON a.id = s.assignment_id
            JOIN enrollments e ON e.course_id = a.course_id
                                AND e.student_id = s.student_id
            WHERE s.assignment_id = %s AND s.student_id = %s
            """,
            (assignment_id, student_id),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="No submission found for this assignment")

    file_name, file_url = row
    if not file_url or not file_url.startswith("database/assignments/"):
        raise HTTPException(status_code=404, detail="Stored file path is invalid")

    path = (PROJECT_ROOT / file_url).resolve()
    if path.parent != UPLOAD_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded file was not found on disk")

    return FileResponse(
        path,
        filename=file_name,
        media_type="application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff"},
    )
