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