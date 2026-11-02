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
from assignment_upload import router as assignment_upload_router
from assistant import router as assistant_router

from auth import authenticate_user

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
LOGIN_URL = "http://127.0.0.1:5500/frontend/index.html"

app = FastAPI(title="ERP AI API")
app.include_router(assignment_upload_router)
app.include_router(assistant_router)

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
    app.mount(
        "/frontend",
        StaticFiles(directory=FRONTEND_DIR),
        name="frontend",
    )

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
        verify_token(access_token)
    except HTTPException:
        response = RedirectResponse(LOGIN_URL, status_code=303)
        response.delete_cookie("access_token", path="/")
        return response

    return FileResponse(HOME_PAGE)


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

        rows = conn.execute(
            """
            SELECT
                a.id,
                a.title,
                a.created_at,
                a.due_date,
                s.submitted_at,
                s.status,
                s.marks_obtained,
                a.max_marks
            FROM enrollments e
            JOIN assignments a ON a.course_id = e.course_id
            LEFT JOIN submissions s
                ON s.assignment_id = a.id
                AND s.student_id = e.student_id
            WHERE e.student_id = %s
              AND e.course_id = %s
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
            """
            SELECT
                s.id,
                s.roll_number,
                s.full_name,
                s.semester,
                s.branch,
                u.email,
                u.created_at
            FROM students s
            JOIN users u ON u.id = s.user_id
            WHERE s.user_id = %s
            """,
            (user["id"],),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Student profile not found")

        student_id, roll_number, full_name, semester, branch, email, created_at = row

        courses = conn.execute(
            """
            SELECT
                c.course_code,
                c.course_name,
                c.semester,
                c.description,
                t.full_name AS teacher_name
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            LEFT JOIN teachers t ON t.id = c.teacher_id
            WHERE e.student_id = %s
            ORDER BY c.course_code
            """,
            (student_id,),
        ).fetchall()

    session_label = f"July-Dec 2026-2027"
    degree = (
        "Bachelor of Technology - Computer Science and Engineering"
        if branch == "CSE"
        else f"Bachelor of Technology - {branch}"
    )

    return {
        "profile": {
            "id": str(student_id),
            "rrn": roll_number,
            "enrollment_no": roll_number,
            "full_name": full_name,
            "email": email,
            "mobile": "9876543210",
            "login_status": "Enabled",
            "admission_status": "Admitted",
            "admission_batch": "2023-24",
            "admission_type": "REGULAR",
            "school": "School of Engineering and Technology",
            "degree_branch": degree,
            "semester": semester,
            "branch": branch,
            "scheme": f"B.Tech-{branch}-2023-24-Department of Computer Engineering (New)",
            "session": session_label,
            "last_login": datetime.now().strftime("%d-%b-%Y %I:%M %p"),
            "created_at": created_at.isoformat() if created_at else None,
            "personal": {
                "dob": "01-01-2005",
                "gender": "Male",
                "nationality": "Indian",
                "religion": "Hindu",
                "category": "General",
                "aadhar": "XXXX-XXXX-1234",
                "father_name": "Ramesh Kumar",
                "mother_name": "Sunita Devi",
            },
            "address": {
                "permanent": "Plot No. 24, Demo Nagar, Jaipur",
                "state": "Rajasthan",
                "pin": "302034",
                "area": "Urban",
                "same_as_permanent": True,
            },
            "qualification": [
                {
                    "level": "12th / Senior Secondary",
                    "board": "RBSE / CBSE",
                    "year": "2023",
                    "percentage": "85.40",
                },
                {
                    "level": "10th / Secondary",
                    "board": "RBSE / CBSE",
                    "year": "2021",
                    "percentage": "88.20",
                },
            ],
            "fees": [
                {
                    "year": "First Year",
                    "semester": "I",
                    "receipt_type": "Academic Fees",
                    "receipt_no": "C/TF/23/1001",
                    "receipt_date": "15/07/2023",
                    "applied": 120000.0,
                    "paid": 120000.0,
                    "outstanding": 0.0,
                },
                {
                    "year": "Second Year",
                    "semester": "III",
                    "receipt_type": "Academic Fees",
                    "receipt_no": "C/TF/24/2104",
                    "receipt_date": "12/07/2024",
                    "applied": 110000.0,
                    "paid": 110000.0,
                    "outstanding": 0.0,
                },
                {
                    "year": "Third Year",
                    "semester": "V",
                    "receipt_type": "Academic Fees",
                    "receipt_no": "C/TF/25/3308",
                    "receipt_date": "10/07/2025",
                    "applied": 110000.0,
                    "paid": 56000.0,
                    "outstanding": 54000.0,
                },
            ],
            "attendance_summary": {
                "session": f"{session_label} ({_roman(semester)})",
                "total_classes": 18,
                "present": 16,
                "absent": 2,
                "od": 0,
                "percent": 88.75,
                "subjects": [
                    {
                        "code": c[0],
                        "name": c[1],
                        "type": "Theory",
                        "total": 10,
                        "present": 9,
                        "absent": 1,
                        "percent": 90.0,
                    }
                    for c in courses[:2]
                ],
            },
            "results": [
                {
                    "semester": "I",
                    "session": "July-Dec 2023-2024",
                    "section": "SF",
                    "registered_courses": 8,
                    "failed_courses": 0,
                    "registered_credits": 22.0,
                    "earned_credits": 22.0,
                    "sgpa": 7.80,
                    "result_date": "20 Jan 2024",
                },
                {
                    "semester": "II",
                    "session": "Jan-June 2023-2024",
                    "section": "SF",
                    "registered_courses": 9,
                    "failed_courses": 0,
                    "registered_credits": 24.0,
                    "earned_credits": 24.0,
                    "sgpa": 7.65,
                    "result_date": "18 Jun 2024",
                },
            ],
            "internal_marks": [
                {
                    "session": session_label,
                    "code": c[0],
                    "name": c[1],
                }
                for c in courses
            ],
            "disciplinary": [],
            "mentor": {
                "mentor_name": courses[0][4] if courses and courses[0][4] else "Faculty Mentor",
                "department": "Computer Science",
                "meeting_notes": "No counselling records found.",
            },
            "courses": [
                {
                    "code": c[0],
                    "name": c[1],
                    "semester": c[2],
                    "description": c[3],
                    "teacher": c[4],
                    "subject_type": "Theory",
                    "credits": 4.0,
                    "course_registered": True,
                    "exam_registered": True,
                }
                for c in courses
            ],
        }
    }


def _roman(num: int) -> str:
    mapping = {
        1: "I", 2: "II", 3: "III", 4: "IV",
        5: "V", 6: "VI", 7: "VII", 8: "VIII",
    }
    return mapping.get(int(num), str(num))


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


@app.get("/api/exams/meta")
def get_exam_meta(access_token: str | None = Cookie(default=None)):
    _require_student(access_token)
    return {
        "sessions": [
            "July-Dec 2026-2027",
            "Jan-June 2025-2026",
            "July-Dec 2024-2025",
            "Jan-June 2024-2025",
        ],
        "exam_names": [
            "INTERNAL EXAM",
            "ASSIGNMENT",
            "INTERNAL PRACTICAL",
            "END TERM",
        ],
    }


@app.get("/api/exams/timetable")
def get_exam_timetable(
    session: str,
    exam_name: str,
    access_token: str | None = Cookie(default=None),
):
    user = _require_student(access_token)
    _student, courses = _student_row(user["id"])

    if not session.strip() or not exam_name.strip():
        raise HTTPException(status_code=400, detail="Session and Exam Name are required")

    # Demo timetable for END TERM / INTERNAL; others intentionally empty.
    if exam_name.upper() not in {"END TERM", "INTERNAL EXAM"}:
        return {"session": session, "exam_name": exam_name, "rows": []}

    slots = ["09:00 AM - 12:00 PM", "01:00 PM - 04:00 PM"]
    rows = []
    for index, course in enumerate(courses[:6]):
        day = 10 + index
        rows.append(
            {
                "exam_date": f"{day:02d}-Dec-2026",
                "slot": slots[index % 2],
                "code": course[0],
                "course": course[1],
                "semester": course[2],
                "type": "Regular",
            }
        )
    return {"session": session, "exam_name": exam_name, "rows": rows}


@app.get("/api/exams/admit-card")
def get_admit_card(access_token: str | None = Cookie(default=None)):
    user = _require_student(access_token)
    student, courses = _student_row(user["id"])
    _id, roll_number, full_name, semester, branch, email = student

    return {
        "admit_card": {
            "student_name": full_name,
            "rrn": roll_number,
            "email": email,
            "semester": semester,
            "branch": branch,
            "exam_name": "END TERM",
            "session": "July-Dec 2026-2027",
            "center": "JECRC University Main Campus",
            "instructions": [
                "Carry this admit card and a valid photo ID to the exam hall.",
                "Report at least 30 minutes before the exam start time.",
                "Electronic devices are not allowed inside the examination hall.",
            ],
            "papers": [
                {
                    "code": c[0],
                    "name": c[1],
                    "date": f"{12 + i:02d}-Dec-2026",
                    "slot": "09:00 AM - 12:00 PM" if i % 2 == 0 else "01:00 PM - 04:00 PM",
                }
                for i, c in enumerate(courses[:5])
            ],
        }
    }


@app.get("/api/exams/backlog")
def get_backlog_courses(
    semester: int | None = None,
    access_token: str | None = Cookie(default=None),
):
    user = _require_student(access_token)
    student, _courses = _student_row(user["id"])
    _id, roll_number, full_name, current_sem, branch, email = student
    selected = semester if semester is not None else 2

    # Demo backlog papers for older semesters.
    demo = [
        {
            "code": "DMA002A",
            "name": "Engineering Mathematics-II",
            "credit": 4.0,
            "semester": 2,
            "course_type": "Theory",
            "grade": "F",
            "exam_fees": 1400.0,
        },
        {
            "code": "BCO101A",
            "name": "Programming Fundamentals",
            "credit": 3.0,
            "semester": 1,
            "course_type": "Theory",
            "grade": "F",
            "exam_fees": 1400.0,
        },
    ]
    rows = [row for row in demo if row["semester"] == selected]

    return {
        "student": {
            "name": full_name,
            "rrn": roll_number,
            "email": email,
            "semester": current_sem,
            "branch": f"B.Tech {branch}",
            "scheme": f"B.Tech-{branch}-2023-24",
            "session": "July-Dec 2026-2027",
        },
        "selected_semester": selected,
        "fee_summary": {
            "processing_fee": 0,
            "certificate_fee": 0,
            "subjects_fee": sum(r["exam_fees"] for r in rows),
            "late_fee": 0,
            "total_fee": sum(r["exam_fees"] for r in rows),
            "paid_fee": 0,
        },
        "courses": rows,
        "warning": "This Is One Time Activity, Kindly Verify Data Before Final Registration.",
    }


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


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"message": "Logged out successfully"}


@app.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    return {"user": user}


@app.get("/")
def root():
    return {"message": "ERP AI API is running"}


@app.get("/signin")
def signin_page():
    """Serve the login page from the API origin to avoid Live Server CORS issues."""
    return FileResponse(FRONTEND_DIR / "index.html")