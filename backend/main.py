import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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

        with psycopg.connect("dbname=erp_ai") as conn:
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
    with psycopg.connect("dbname=erp_ai") as conn:
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