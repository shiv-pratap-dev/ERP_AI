"""Faculty Establishment → Transactions (Leave / OD / Comp-Off / Service Book)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from faculty_api import _require_teacher

router = APIRouter(prefix="/api/faculty/establishment", tags=["establishment"])

LEAVE_TYPES = (
    ("Casual Leave", 12.0),
    ("Earned Leave", 10.0),
    ("Medical Leave", 10.0),
    ("Vacation", 3.0),
)
APPROVAL_PATH = "HOD CS -> Dean Engineering -> Registrar -> Assistant Registrar"
HOLIDAYS_2026 = (
    (date(2026, 1, 26), "Republic Day"),
    (date(2026, 3, 14), "Holi"),
    (date(2026, 8, 15), "Independence Day"),
    (date(2026, 10, 2), "Gandhi Jayanti"),
    (date(2026, 10, 20), "Diwali"),
    (date(2026, 11, 5), "Guru Nanak Jayanti"),
    (date(2026, 12, 25), "Christmas"),
)


class LeaveApplyIn(BaseModel):
    leave_name: str
    from_date: str
    to_date: str
    reason: str = Field(min_length=1, max_length=500)
    half_day: bool = False


class LeaveCancelIn(BaseModel):
    application_id: str


class OdApplyIn(BaseModel):
    from_date: str
    to_date: str
    from_time: str = "09:00"
    to_time: str = "17:00"
    place: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)


class CompOffApplyIn(BaseModel):
    working_date: str
    in_time: str
    out_time: str
    reason: str = Field(min_length=1, max_length=500)


def ensure_establishment_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leave_balances (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            leave_name VARCHAR(60) NOT NULL,
            period_label VARCHAR(30) NOT NULL,
            opening_balance NUMERIC(8,2) NOT NULL DEFAULT 0,
            credit NUMERIC(8,2) NOT NULL DEFAULT 0,
            taken NUMERIC(8,2) NOT NULL DEFAULT 0,
            UNIQUE (teacher_id, leave_name, period_label)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leave_applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            leave_name VARCHAR(60) NOT NULL,
            from_date DATE NOT NULL,
            to_date DATE NOT NULL,
            days NUMERIC(8,2) NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS od_applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            from_date DATE NOT NULL,
            to_date DATE NOT NULL,
            from_time VARCHAR(10) NOT NULL,
            to_time VARCHAR(10) NOT NULL,
            place TEXT NOT NULL,
            purpose TEXT NOT NULL,
            status VARCHAR(30) NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'Approved', 'Rejected', 'Cancelled')),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS comp_off_applications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            working_date DATE NOT NULL,
            expiry_date DATE NOT NULL,
            in_time VARCHAR(12) NOT NULL,
            out_time VARCHAR(12) NOT NULL,
            working_hours VARCHAR(12) NOT NULL,
            no_of_day NUMERIC(8,2) NOT NULL DEFAULT 1,
            reason TEXT NOT NULL,
            approval_path TEXT NOT NULL,
            status VARCHAR(40) NOT NULL DEFAULT 'Pending'
                CHECK (status IN ('Pending', 'Approved & Credited', 'Rejected', 'Cancelled')),
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.commit()


def _parse_date(value: str) -> date:
    raw = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Enter a valid date")


def _parse_time(value: str) -> tuple[int, int, int]:
    raw = (value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.hour, parsed.minute, parsed.second
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Enter a valid time (HH:MM)")


def _fmt_date(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d-%b-%Y")


def _fmt_stamp(value: datetime | None) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone()
    return local.strftime("%d-%b-%Y %I:%M %p")


def _days_between(start: date, end: date, half_day: bool = False) -> float:
    if end < start:
        raise HTTPException(status_code=400, detail="To Date cannot be before From Date")
    days = (end - start).days + 1
    if half_day and days == 1:
        return 0.5
    return float(days)


def _period_label(today: date | None = None) -> str:
    today = today or date.today()
    return f"YEARLY-{today.year}"


def _seed_teacher(conn, teacher_id: str, department: str) -> None:
    period = _period_label()
    for leave_name, credit in LEAVE_TYPES:
        exists = conn.execute(
            """
            SELECT 1 FROM leave_balances
            WHERE teacher_id = %s AND leave_name = %s AND period_label = %s
            """,
            (teacher_id, leave_name, period),
        ).fetchone()
        if not exists:
            taken = 0.0 if leave_name != "Casual Leave" else 0.0
            conn.execute(
                """
                INSERT INTO leave_balances
                    (teacher_id, leave_name, period_label, opening_balance, credit, taken)
                VALUES (%s, %s, %s, 0, %s, %s)
                """,
                (teacher_id, leave_name, period, credit, taken),
            )

    has_comp = conn.execute(
        "SELECT 1 FROM comp_off_applications WHERE teacher_id = %s LIMIT 1",
        (teacher_id,),
    ).fetchone()
    if not has_comp:
        samples = (
            (date(2026, 8, 15), "08:13:30", "12:36:17", "04:22:47", 1.0, "Holiday duty coverage"),
            (date(2026, 10, 2), "09:05:10", "14:20:40", "05:15:30", 1.0, "Event duty on campus"),
        )
        for working_date, in_time, out_time, hours, days, reason in samples:
            conn.execute(
                """
                INSERT INTO comp_off_applications (
                    teacher_id, working_date, expiry_date, in_time, out_time,
                    working_hours, no_of_day, reason, approval_path, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Approved & Credited')
                """,
                (
                    teacher_id,
                    working_date,
                    working_date + timedelta(days=90),
                    in_time,
                    out_time,
                    hours,
                    days,
                    reason,
                    APPROVAL_PATH.replace("HOD CS", f"HOD {department.split()[0] if department else 'CS'}"),
                ),
            )
    conn.commit()


def _balance_rows(conn, teacher_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT leave_name, period_label, opening_balance, credit, taken
        FROM leave_balances
        WHERE teacher_id = %s
        ORDER BY leave_name
        """,
        (teacher_id,),
    ).fetchall()
    result = []
    for leave_name, period, opening, credit, taken in rows:
        opening_f = float(opening or 0)
        credit_f = float(credit or 0)
        taken_f = float(taken or 0)
        total = opening_f + credit_f
        result.append(
            {
                "leave_name": leave_name,
                "period": period,
                "opening_balance": opening_f,
                "credit": credit_f,
                "total": total,
                "taken": taken_f,
                "balance": round(total - taken_f, 2),
            }
        )
    return result


@router.get("/leave-card")
def leave_card(access_token: str | None = Cookie(default=None)):
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        _seed_teacher(conn, teacher_id, teacher.get("department") or "Computer Science")
        balances = _balance_rows(conn, teacher_id)
        updated = conn.execute(
            """
            SELECT MAX(updated_at) FROM (
                SELECT NOW() AS updated_at
                UNION ALL
                SELECT MAX(applied_at) FROM leave_applications WHERE teacher_id = %s
            ) t
            """,
            (teacher_id,),
        ).fetchone()
    stamp = updated[0] if updated and updated[0] else datetime.now(timezone.utc)
    return {
        "teacher": {
            "employee_id": teacher["employee_id"],
            "full_name": teacher["full_name"],
            "department": teacher.get("department") or "Computer Science",
        },
        "last_updated": _fmt_stamp(stamp if isinstance(stamp, datetime) else datetime.now(timezone.utc)),
        "balances": balances,
    }


@router.get("/leave-applications")
def leave_applications(access_token: str | None = Cookie(default=None)):
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        rows = conn.execute(
            """
            SELECT id, leave_name, from_date, to_date, days, reason, status, applied_at
            FROM leave_applications
            WHERE teacher_id = %s
            ORDER BY applied_at DESC
            """,
            (teacher_id,),
        ).fetchall()
    return {
        "applications": [
            {
                "id": str(r[0]),
                "leave_name": r[1],
                "from_date": _fmt_date(r[2]),
                "to_date": _fmt_date(r[3]),
                "days": float(r[4]),
                "reason": r[5],
                "status": r[6],
                "applied_at": _fmt_stamp(r[7]),
            }
            for r in rows
        ]
    }


@router.post("/leave-applications")
def apply_leave(body: LeaveApplyIn, access_token: str | None = Cookie(default=None)):
    _teacher, teacher_id = _require_teacher(access_token)
    leave_name = body.leave_name.strip()
    if leave_name not in {name for name, _ in LEAVE_TYPES}:
        raise HTTPException(status_code=400, detail="Select a valid leave type")
    start = _parse_date(body.from_date)
    end = _parse_date(body.to_date)
    days = _days_between(start, end, body.half_day)
    reason = body.reason.strip()

    with get_connection() as conn:
        ensure_establishment_schema(conn)
        period = _period_label()
        bal = conn.execute(
            """
            SELECT opening_balance, credit, taken
            FROM leave_balances
            WHERE teacher_id = %s AND leave_name = %s AND period_label = %s
            """,
            (teacher_id, leave_name, period),
        ).fetchone()
        if not bal:
            raise HTTPException(status_code=404, detail="Leave balance not found")
        available = float(bal[0] or 0) + float(bal[1] or 0) - float(bal[2] or 0)
        if days > available:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Available: {available:.2f}",
            )
        row = conn.execute(
            """
            INSERT INTO leave_applications
                (teacher_id, leave_name, from_date, to_date, days, reason, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'Pending')
            RETURNING id, status, applied_at
            """,
            (teacher_id, leave_name, start, end, days, reason),
        ).fetchone()
        conn.execute(
            """
            UPDATE leave_balances
            SET taken = taken + %s
            WHERE teacher_id = %s AND leave_name = %s AND period_label = %s
            """,
            (days, teacher_id, leave_name, period),
        )
        conn.commit()
    return {
        "message": "Leave application submitted",
        "application": {
            "id": str(row[0]),
            "status": row[1],
            "applied_at": _fmt_stamp(row[2]),
            "days": days,
        },
    }


@router.post("/leave-applications/cancel")
def cancel_leave(body: LeaveCancelIn, access_token: str | None = Cookie(default=None)):
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        row = conn.execute(
            """
            SELECT id, leave_name, days, status, from_date
            FROM leave_applications
            WHERE id = %s AND teacher_id = %s
            """,
            (body.application_id, teacher_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Leave application not found")
        if row[3] != "Pending":
            raise HTTPException(status_code=400, detail="Only pending leave can be cancelled")
        period = _period_label(row[4])
        conn.execute(
            """
            UPDATE leave_applications
            SET status = 'Cancelled', updated_at = NOW()
            WHERE id = %s
            """,
            (body.application_id,),
        )
        conn.execute(
            """
            UPDATE leave_balances
            SET taken = GREATEST(taken - %s, 0)
            WHERE teacher_id = %s AND leave_name = %s AND period_label = %s
            """,
            (float(row[2]), teacher_id, row[1], period),
        )
        conn.commit()
    return {"message": "Leave application cancelled"}


@router.get("/holidays")
def holidays(access_token: str | None = Cookie(default=None)):
    _require_teacher(access_token)
    today = date.today()
    items = [
        {
            "date": _fmt_date(day),
            "name": name,
            "upcoming": day >= today,
        }
        for day, name in HOLIDAYS_2026
    ]
    return {"year": today.year, "holidays": items}


@router.get("/od-applications")
def list_od(access_token: str | None = Cookie(default=None)):
    _teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        rows = conn.execute(
            """
            SELECT id, from_date, to_date, from_time, to_time, place, purpose, status, applied_at
            FROM od_applications
            WHERE teacher_id = %s
            ORDER BY applied_at DESC
            """,
            (teacher_id,),
        ).fetchall()
    return {
        "applications": [
            {
                "id": str(r[0]),
                "from_date": _fmt_date(r[1]),
                "to_date": _fmt_date(r[2]),
                "from_time": r[3],
                "to_time": r[4],
                "place": r[5],
                "purpose": r[6],
                "status": r[7],
                "applied_at": _fmt_stamp(r[8]),
            }
            for r in rows
        ]
    }


@router.post("/od-applications")
def apply_od(body: OdApplyIn, access_token: str | None = Cookie(default=None)):
    _teacher, teacher_id = _require_teacher(access_token)
    start = _parse_date(body.from_date)
    end = _parse_date(body.to_date)
    if end < start:
        raise HTTPException(status_code=400, detail="To Date cannot be before From Date")
    _parse_time(body.from_time)
    _parse_time(body.to_time)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        row = conn.execute(
            """
            INSERT INTO od_applications
                (teacher_id, from_date, to_date, from_time, to_time, place, purpose, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Pending')
            RETURNING id, status, applied_at
            """,
            (
                teacher_id,
                start,
                end,
                body.from_time.strip(),
                body.to_time.strip(),
                body.place.strip(),
                body.purpose.strip(),
            ),
        ).fetchone()
        conn.commit()
    return {
        "message": "OD application submitted",
        "application": {
            "id": str(row[0]),
            "status": row[1],
            "applied_at": _fmt_stamp(row[2]),
        },
    }


@router.get("/comp-off")
def list_comp_off(access_token: str | None = Cookie(default=None)):
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        _seed_teacher(conn, teacher_id, teacher.get("department") or "Computer Science")
        rows = conn.execute(
            """
            SELECT id, working_date, expiry_date, in_time, out_time, working_hours,
                   no_of_day, reason, approval_path, status, applied_at
            FROM comp_off_applications
            WHERE teacher_id = %s
            ORDER BY working_date DESC
            """,
            (teacher_id,),
        ).fetchall()
    return {
        "approval_path": APPROVAL_PATH,
        "applications": [
            {
                "id": str(r[0]),
                "working_date": _fmt_date(r[1]),
                "expiry_date": _fmt_date(r[2]),
                "in_time": r[3],
                "out_time": r[4],
                "working_hours": r[5],
                "no_of_day": float(r[6]),
                "reason": r[7],
                "approval_path": r[8],
                "status": r[9],
                "applied_at": _fmt_stamp(r[10]),
            }
            for r in rows
        ],
    }


@router.post("/comp-off")
def apply_comp_off(body: CompOffApplyIn, access_token: str | None = Cookie(default=None)):
    teacher, teacher_id = _require_teacher(access_token)
    working_date = _parse_date(body.working_date)
    in_h, in_m, in_s = _parse_time(body.in_time)
    out_h, out_m, out_s = _parse_time(body.out_time)
    start_seconds = in_h * 3600 + in_m * 60 + in_s
    end_seconds = out_h * 3600 + out_m * 60 + out_s
    if end_seconds <= start_seconds:
        raise HTTPException(status_code=400, detail="Out Time must be after In Time")
    diff = end_seconds - start_seconds
    hours = diff // 3600
    minutes = (diff % 3600) // 60
    seconds = diff % 60
    working_hours = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    no_of_day = 1.0 if diff >= 4 * 3600 else 0.5
    dept = (teacher.get("department") or "Computer Science").split()[0]
    path = APPROVAL_PATH.replace("HOD CS", f"HOD {dept}")

    with get_connection() as conn:
        ensure_establishment_schema(conn)
        row = conn.execute(
            """
            INSERT INTO comp_off_applications (
                teacher_id, working_date, expiry_date, in_time, out_time,
                working_hours, no_of_day, reason, approval_path, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending')
            RETURNING id, status, applied_at
            """,
            (
                teacher_id,
                working_date,
                working_date + timedelta(days=90),
                f"{in_h:02d}:{in_m:02d}:{in_s:02d}",
                f"{out_h:02d}:{out_m:02d}:{out_s:02d}",
                working_hours,
                no_of_day,
                body.reason.strip(),
                path,
            ),
        ).fetchone()
        conn.commit()
    return {
        "message": "Comp-Off application submitted",
        "application": {
            "id": str(row[0]),
            "status": row[1],
            "working_hours": working_hours,
            "no_of_day": no_of_day,
            "applied_at": _fmt_stamp(row[2]),
        },
    }


@router.get("/service-book")
def service_book(access_token: str | None = Cookie(default=None)):
    teacher, teacher_id = _require_teacher(access_token)
    with get_connection() as conn:
        ensure_establishment_schema(conn)
        joined = conn.execute(
            "SELECT created_at FROM teachers WHERE id = %s",
            (teacher_id,),
        ).fetchone()
    join_date = joined[0].date() if joined and joined[0] else date(2020, 7, 1)
    if isinstance(join_date, datetime):
        join_date = join_date.date()
    return {
        "profile": {
            "employee_id": teacher["employee_id"],
            "full_name": teacher["full_name"],
            "department": teacher.get("department") or "Computer Science",
            "designation": "Assistant Professor",
            "email": teacher.get("email") or "",
            "date_of_joining": _fmt_date(join_date),
            "employment_type": "Permanent",
            "pay_scale": "Academic Level 10",
            "current_status": "Active",
        },
        "history": [
            {
                "effective_date": _fmt_date(join_date),
                "event": "Joined as Assistant Professor",
                "remarks": "Initial appointment",
            },
            {
                "effective_date": _fmt_date(join_date + timedelta(days=365)),
                "event": "Confirmation",
                "remarks": "Probation completed",
            },
            {
                "effective_date": _fmt_date(date(join_date.year + 3, join_date.month, min(join_date.day, 28))),
                "event": "Annual Increment",
                "remarks": "Regular increment credited",
            },
        ],
    }
