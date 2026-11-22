import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from dotenv import load_dotenv
from database import get_connection
from fastapi import FastAPI, HTTPException, Depends, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from erp_ai.tools.lms_tools.assignment_upload import (
    router as assignment_upload_router,
)
from erp_ai.tools.examination_tools import (
    get_admit_card as load_admit_card,
    get_backlog_courses as load_backlog_courses,
    get_exam_meta as load_exam_meta,
    get_exam_timetable as load_exam_timetable,
)
from assistant import router as assistant_router
from attendance_api import router as attendance_router
from attendance_register_api import router as attendance_register_router
from extra_class_api import router as extra_class_router
from faculty_api import ensure_lms_schema, router as faculty_router
from mark_entry_api import router as mark_entry_router
from establishment_api import router as establishment_router
from obe_api import router as obe_router
from payroll_api import router as payroll_router
from teaching_plan_api import router as teaching_plan_router

from auth import authenticate_user, change_user_password

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("JWT_SECRET_KEY")

if not SECRET_KEY or SECRET_KEY == "paste-your-generated-secret-here":
    raise RuntimeError("Set a real JWT_SECRET_KEY in your .env file")

ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 30

FRONTEND_DIR = BASE_DIR / "frontend"
HOME_PAGE = FRONTEND_DIR / "home.html"
FACULTY_HOME_PAGE = FRONTEND_DIR / "faculty" / "home.html"
LOGIN_URL = "http://127.0.0.1:8000/signin"

app = FastAPI(title="ERP AI API")
app.include_router(assignment_upload_router)
app.include_router(assistant_router)
app.include_router(faculty_router)
app.include_router(teaching_plan_router)
app.include_router(attendance_router)
app.include_router(attendance_register_router)
app.include_router(extra_class_router)
app.include_router(mark_entry_router)
app.include_router(payroll_router)
app.include_router(establishment_router)
app.include_router(obe_router)

# Serve existing image/assets folder using both spellings
ASSETS_DIR = FRONTEND_DIR / "assests"

if ASSETS_DIR.exists():
    app.mount(
        "/assests",
        StaticFiles(directory=ASSETS_DIR),
        name="assests",
    )
    app.mount(
        "/assets",
        StaticFiles(directory=ASSETS_DIR),
        name="assests",
    )

    app.mount(
        "/LMS",

        StaticFiles(directory=FRONTEND_DIR / "LMS", html=True),

        name="LMS",
    )
    academic_dir = FRONTEND_DIR / "Academic"
    if academic_dir.exists():
        app.mount(
            "/Academic",
            StaticFiles(directory=academic_dir, html=True),
            name="Academic",
        )
    examination_dir = FRONTEND_DIR / "Examination"
    if examination_dir.exists():
        app.mount(
            "/Examination",
            StaticFiles(directory=examination_dir, html=True),
            name="Examination",
        )
    pay_online_dir = FRONTEND_DIR / "PayOnline"
    if pay_online_dir.exists():
        app.mount(
            "/PayOnline",
            StaticFiles(directory=pay_online_dir, html=True),
            name="PayOnline",
        )
    faculty_lms_dir = FRONTEND_DIR / "faculty" / "LMS"
    if faculty_lms_dir.exists():
        app.mount(
            "/faculty/LMS",
            StaticFiles(directory=faculty_lms_dir, html=True),
            name="faculty_lms",
        )
    # Mounted only when the folder exists, so reload after the page is added.
    faculty_academic_dir = FRONTEND_DIR / "faculty" / "Academic"
    if faculty_academic_dir.exists():
        app.mount(
            "/faculty/Academic",
            StaticFiles(directory=faculty_academic_dir, html=True),
            name="faculty_academic",
        )
    faculty_exam_dir = FRONTEND_DIR / "faculty" / "Examination"
    if faculty_exam_dir.exists():
        app.mount(
            "/faculty/Examination",
            StaticFiles(directory=faculty_exam_dir, html=True),
            name="faculty_examination",
        )
    faculty_payroll_dir = FRONTEND_DIR / "faculty" / "Payroll"
    if faculty_payroll_dir.exists():
        app.mount(
            "/faculty/Payroll",
            StaticFiles(directory=faculty_payroll_dir, html=True),
            name="faculty_payroll",
        )
    faculty_establishment_dir = FRONTEND_DIR / "faculty" / "Establishment"
    if faculty_establishment_dir.exists():
        app.mount(
            "/faculty/Establishment",
            StaticFiles(directory=faculty_establishment_dir, html=True),
            name="faculty_establishment",
        )
    faculty_obe_dir = FRONTEND_DIR / "faculty" / "OBE"
    if faculty_obe_dir.exists():
        app.mount(
            "/faculty/OBE",
            StaticFiles(directory=faculty_obe_dir, html=True),
            name="faculty_obe",
        )
    app.mount(
        "/frontend",
        StaticFiles(directory=FRONTEND_DIR),
        name="frontend",
    )

@app.middleware("http")
async def disable_static_cache(request, call_next):
    """Prevent browsers from keeping stale LMS/module HTML/JS during local dev."""
    response = await call_next(request)
    path = request.url.path.lower()
    if (
        path.endswith((".html", ".js", ".css"))
        or path.startswith("/lms/")
        or path.startswith("/frontend/")
        or path.startswith("/academic/")
        or path.startswith("/examination/")
        or path.startswith("/payonline/")
        or path.startswith("/faculty/")
    ):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:5501",
        "http://localhost:5501",
        "http://127.0.0.1:5502",
        "http://localhost:5502",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "null",  # file:// opens during local testing
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def create_access_token(user: dict) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user["id"],
        "email": user["email"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_EXPIRE_MINUTES),
    }

    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
        )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        with get_connection() as conn:
            user = conn.execute(
                """
                SELECT id, email, role
                FROM users
                WHERE id = %s AND is_active = TRUE
                """,
                (user_id,),
            ).fetchone()

        if not user:
            raise HTTPException(
                status_code=401,
                detail="User not found or inactive",
            )

        return {
            "id": str(user[0]),
            "email": user[1],
            "role": user[2],
        }

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    return verify_token(credentials.credentials)


@app.post("/login")
def login(data: LoginRequest, response: Response):
    with get_connection() as conn:
        user = authenticate_user(
            conn,
            data.email.strip(),
            data.password,
        )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    token = create_access_token(user)

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=False,  # Local HTTP development only
        samesite="lax",
        max_age=TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return {
        "message": "Login successful",
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


@app.get("/home")
def protected_home(access_token: str | None = Cookie(default=None)):
    if not access_token:
        return RedirectResponse(LOGIN_URL, status_code=303)

    try:
        user = verify_token(access_token)
    except HTTPException:
        response = RedirectResponse(LOGIN_URL, status_code=303)
        response.delete_cookie("access_token", path="/")
        return response

    if user.get("role") == "teacher":
        return RedirectResponse("/faculty/home", status_code=303)

    return FileResponse(HOME_PAGE)


@app.get("/faculty/home")
def faculty_home(access_token: str | None = Cookie(default=None)):
    if not access_token:
        return RedirectResponse(LOGIN_URL, status_code=303)

    try:
        user = verify_token(access_token)
    except HTTPException:
        response = RedirectResponse(LOGIN_URL, status_code=303)
        response.delete_cookie("access_token", path="/")
        return response

    if user.get("role") != "teacher":
        return RedirectResponse("/home", status_code=303)

    if not FACULTY_HOME_PAGE.exists():
        raise HTTPException(status_code=404, detail="Faculty home page missing")
    return FileResponse(FACULTY_HOME_PAGE)


@app.get("/session-user")
def get_session_user(access_token: str | None = Cookie(default=None)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = verify_token(access_token)
    return {"user": user}

@app.get("/api/courses")
def get_student_courses(
    semester: int | None = None,
    access_token: str | None = Cookie(default=None),
):
    # 1. Verify login session
    if not access_token:
        raise HTTPException(
            status_code=401,
            detail="Not logged in",
        )

    user = verify_token(access_token)

    # 2. Allow students only
    if user["role"] != "student":
        raise HTTPException(
            status_code=403,
            detail="Only students can access enrolled courses",
        )

    # 3. Validate semester if provided
    if semester is not None and not 1 <= semester <= 8:
        raise HTTPException(
            status_code=400,
            detail="Semester must be between 1 and 8",
        )

    # 4. Fetch this student's enrolled courses
    with get_connection() as conn:
        student = conn.execute(
            """
            SELECT id, semester
            FROM students
            WHERE user_id = %s
            """,
            (user["id"],),
        ).fetchone()

        if not student:
            raise HTTPException(
                status_code=404,
                detail="Student profile not found",
            )

        selected_semester = semester if semester is not None else student[1]

        courses = conn.execute(
            """
            SELECT
                c.id,
                c.course_code,
                c.course_name,
                c.semester,
                c.description,
                t.full_name AS teacher_name
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            LEFT JOIN teachers t ON t.id = c.teacher_id
            WHERE e.student_id = %s
              AND c.semester = %s
            ORDER BY c.course_code
            """,
            (student[0], selected_semester),
        ).fetchall()

    # 5. Return JSON data
    return {
        "semester": selected_semester,
        "courses": [
            {
                "id": str(course[0]),
                "code": course[1],
                "name": course[2],
                "semester": course[3],
                "description": course[4],
                "teacher": course[5],
            }
            for course in courses
        ],
    }


@app.get("/api/courses/{course_id}/assignments")
def get_course_assignments(
    course_id: str,
    access_token: str | None = Cookie(default=None),
):
    # 1. Authenticate the user
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = verify_token(access_token)

    if user["role"] != "student":
        raise HTTPException(
            status_code=403,
            detail="Only students can access assignments",
        )

    # 2. Fetch assignments only for a course the student is enrolled in
    with get_connection() as conn:
        student = conn.execute(
            """
            SELECT id
            FROM students
            WHERE user_id = %s
            """,
            (user["id"],),
        ).fetchone()

        if not student:
            raise HTTPException(
                status_code=404,
                detail="Student profile not found",
            )

        ensure_lms_schema(conn)
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.title,
                a.created_at,
                a.due_date,
                s.submitted_at,
                s.status,
                CASE
                    WHEN COALESCE(s.display_to_student, TRUE) THEN s.marks_obtained
                    ELSE NULL
                END,
                a.max_marks
            FROM enrollments e
            JOIN assignments a ON a.course_id = e.course_id
            LEFT JOIN submissions s
                ON s.assignment_id = a.id
                AND s.student_id = e.student_id
            WHERE e.student_id = %s
              AND e.course_id = %s
              AND (
                NOT EXISTS (
                    SELECT 1 FROM assignment_recipients r
                    WHERE r.assignment_id = a.id
                )
                OR EXISTS (
                    SELECT 1 FROM assignment_recipients r
                    WHERE r.assignment_id = a.id
                      AND r.student_id = e.student_id
                )
              )
            ORDER BY a.due_date DESC
            """,
            (student[0], course_id),
        ).fetchall()

    now = datetime.now(timezone.utc)

    assignments = []

    for row in rows:
        assignment_id, title, created_at, due_date, submitted_at, submission_status, marks, max_marks = row

        if submitted_at:
            status = submission_status.title()
        elif due_date < now:
            status = "Expired"
        else:
            status = "Pending"

        assignments.append({
            "id": str(assignment_id),
            "subject": title,
            "submission_date": (
                submitted_at.strftime("%d-%b-%Y")
                if submitted_at else (
                    created_at.strftime("%d-%b-%Y")
                    if created_at else None
                )
            ),
            "due_date": due_date.strftime("%d/%m/%Y %H:%M:%S"),
            "status": status,
            "marks_obtained": float(marks) if marks is not None else None,
            "max_marks": float(max_marks),
        })

    return {"assignments": assignments}

@app.get("/api/student/profile")
def get_student_profile(access_token: str | None = Cookie(default=None)):
    """Return the logged-in student's academic profile for Student Complete Detail."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = verify_token(access_token)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Only students can view this profile")

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM students WHERE user_id = %s",
            (user["id"],),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Student profile not found")

    from erp_ai.tools.academic_tools import get_student_profile as load_profile

    try:
        profile = load_profile(str(row[0]))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"profile": profile}


@app.get("/api/student/attendance")
def get_student_attendance(access_token: str | None = Cookie(default=None)):
    """Live attendance for the logged-in student, mirrored from faculty marks."""
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = verify_token(access_token)
    if user["role"] != "student":
        raise HTTPException(
            status_code=403,
            detail="Only students can view attendance",
        )

    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM students WHERE user_id = %s",
            (user["id"],),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Student profile not found")

    from erp_ai.tools.academic_tools import get_attendance_summary

    try:
        summary = get_attendance_summary(str(row[0]))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {"attendance": summary}



def _require_student(access_token: str | None) -> dict:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = verify_token(access_token)
    if user["role"] != "student":
        raise HTTPException(status_code=403, detail="Only students can access examination")
    return user


def _student_row(user_id: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT s.id, s.roll_number, s.full_name, s.semester, s.branch, u.email
            FROM students s
            JOIN users u ON u.id = s.user_id
            WHERE s.user_id = %s
            """,
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Student profile not found")

        courses = conn.execute(
            """
            SELECT c.course_code, c.course_name, c.semester
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            WHERE e.student_id = %s
            ORDER BY c.course_code
            """,
            (row[0],),
        ).fetchall()
    return row, courses


def _student_id_for_user(user_id: str) -> str:
    student, _courses = _student_row(user_id)
    return str(student[0])


@app.get("/api/exams/meta")
def get_exam_meta(access_token: str | None = Cookie(default=None)):
    _require_student(access_token)
    return load_exam_meta()


@app.get("/api/exams/timetable")
def get_exam_timetable(
    session: str,
    exam_name: str,
    access_token: str | None = Cookie(default=None),
):
    user = _require_student(access_token)
    if not session.strip() or not exam_name.strip():
        raise HTTPException(
            status_code=400,
            detail="Session and Exam Name are required",
        )
    try:
        return load_exam_timetable(
            _student_id_for_user(user["id"]),
            session=session,
            exam_name=exam_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/exams/admit-card")
def get_admit_card(access_token: str | None = Cookie(default=None)):
    user = _require_student(access_token)
    try:
        return load_admit_card(_student_id_for_user(user["id"]))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/exams/backlog")
def get_backlog_courses(
    semester: int | None = None,
    access_token: str | None = Cookie(default=None),
):
    user = _require_student(access_token)
    try:
        return load_backlog_courses(
            _student_id_for_user(user["id"]),
            semester=semester,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class BacklogRegisterRequest(BaseModel):
    semester: int
    course_codes: list[str]


@app.post("/api/exams/backlog/register")
def register_backlog(
    data: BacklogRegisterRequest,
    access_token: str | None = Cookie(default=None),
):
    _require_student(access_token)
    if not data.course_codes:
        raise HTTPException(status_code=400, detail="Select at least one backlog course")

    return {
        "message": "Backlog registration saved. Proceed to Pay Online to complete payment.",
        "semester": data.semester,
        "course_codes": data.course_codes,
        "redirect": "/Examination/examination.html?view=backlog",
    }


@app.get("/api/payments/overview")
def get_payments_overview(access_token: str | None = Cookie(default=None)):
    """Student fee ledger and payment options for Pay Online."""
    user = _require_student(access_token)
    student, _courses = _student_row(user["id"])
    _id, roll_number, full_name, semester, branch, email = student

    ledger = [
        {
            "receipt_type": "Academic Fees",
            "semester": "I",
            "payable": 120000.0,
            "paid": 120000.0,
            "balance": 0.0,
        },
        {
            "receipt_type": "Backlog Exam",
            "semester": "II",
            "payable": 500.0,
            "paid": 500.0,
            "balance": 0.0,
        },
        {
            "receipt_type": "Academic Fees",
            "semester": "II",
            "payable": 110000.0,
            "paid": 110000.0,
            "balance": 0.0,
        },
        {
            "receipt_type": "Academic Fees",
            "semester": "III",
            "payable": 110000.0,
            "paid": 110000.0,
            "balance": 0.0,
        },
        {
            "receipt_type": "Academic Fees",
            "semester": "IV",
            "payable": 110000.0,
            "paid": 110000.0,
            "balance": 0.0,
        },
        {
            "receipt_type": "Academic Fees",
            "semester": "V",
            "payable": 110000.0,
            "paid": 56000.0,
            "balance": 54000.0,
        },
        {
            "receipt_type": "Academic Fees",
            "semester": "VII",
            "payable": 110000.0,
            "paid": 0.0,
            "balance": 110000.0,
        },
        {
            "receipt_type": "Academic Fees",
            "semester": "VIII",
            "payable": 110000.0,
            "paid": 0.0,
            "balance": 110000.0,
        },
    ]

    receipts = [
        {
            "receipt_type": "Academic Fees",
            "receipt_no": "C/TF/23/5712",
            "date": "02/07/2023",
            "semester": "I",
            "pay_type": "O",
            "amount": 120000.0,
        },
        {
            "receipt_type": "Backlog Exam",
            "receipt_no": "C/TF/24/8821",
            "date": "23/10/2024",
            "semester": "II",
            "pay_type": "O",
            "amount": 500.0,
        },
        {
            "receipt_type": "Academic Fees",
            "receipt_no": "C/TF/24/9104",
            "date": "12/07/2024",
            "semester": "III",
            "pay_type": "O",
            "amount": 110000.0,
        },
        {
            "receipt_type": "Academic Fees",
            "receipt_no": "C/TF/25/3308",
            "date": "10/07/2025",
            "semester": "V",
            "pay_type": "O",
            "amount": 56000.0,
        },
        {
            "receipt_type": "Academic Fees",
            "receipt_no": "C/TF/25/43622",
            "date": "22/01/2026",
            "semester": "V",
            "pay_type": "O",
            "amount": 96800.0,
        },
    ]

    return {
        "student": {
            "name": full_name,
            "rrn": roll_number,
            "email": email,
            "mobile": "9876543210",
            "school": "School of Engineering and Technology",
            "degree": "Bachelor of Technology",
            "branch": f"Computer Science and Engineering ({branch})",
            "semester": semester,
        },
        "receipt_types": ["Academic Fees", "Backlog Exam"],
        "semesters": ["I", "II", "III", "IV", "V", "VI", "VII", "VIII"],
        "ledger": ledger,
        "previous_receipts": receipts,
    }


class PaymentRequest(BaseModel):
    receipt_type: str
    semester: str


@app.post("/api/payments/pay")
def make_payment(
    data: PaymentRequest,
    access_token: str | None = Cookie(default=None),
):
    """Simulate an online payment for the selected fee head."""
    _require_student(access_token)

    overview = get_payments_overview(access_token)
    match = next(
        (
            row
            for row in overview["ledger"]
            if row["receipt_type"] == data.receipt_type
            and row["semester"] == data.semester
        ),
        None,
    )

    if not match:
        raise HTTPException(
            status_code=404,
            detail="No fee record found for selected receipt type and semester",
        )

    if match["balance"] <= 0:
        return {
            "status": "already_paid",
            "message": "Fees already paid for selected semester.",
            "amount": 0,
            "receipt_no": None,
        }

    receipt_no = f"C/TF/26/{datetime.now().strftime('%H%M%S')}"
    return {
        "status": "paid",
        "message": (
            f"Payment of ₹{match['balance']:.2f} successful for "
            f"{data.receipt_type} (Sem {data.semester})."
        ),
        "amount": match["balance"],
        "receipt_no": receipt_no,
        "receipt_type": data.receipt_type,
        "semester": data.semester,
        "date": datetime.now().strftime("%d/%m/%Y"),
    }


@app.post("/api/change-password")
def change_password(
    data: ChangePasswordRequest,
    access_token: str | None = Cookie(default=None),
):
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    user = verify_token(access_token)
    current = (data.current_password or "").strip()
    new_password = (data.new_password or "").strip()

    if len(new_password) < 6:
        raise HTTPException(
            status_code=400,
            detail="New password must be at least 6 characters",
        )
    if new_password == current:
        raise HTTPException(
            status_code=400,
            detail="New password must be different from the current password",
        )

    with get_connection() as conn:
        ok, error = change_user_password(
            conn,
            user["id"],
            current,
            new_password,
        )

    if not ok:
        raise HTTPException(status_code=400, detail=error or "Password change failed")

    return {"message": "Password updated successfully"}


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"message": "Logged out successfully"}


@app.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"user": user}


@app.get("/")
def root():
    return RedirectResponse("/signin", status_code=303)


@app.get("/signin")
def signin_page():
    """Serve the login page from the API origin to avoid Live Server CORS issues."""
    return FileResponse(FRONTEND_DIR / "index.html")