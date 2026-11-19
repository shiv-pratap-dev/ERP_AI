"""Faculty Payroll → Reports → Employee PaySlip."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from fastapi import APIRouter, Cookie, HTTPException, Query
from pydantic import BaseModel, Field

from faculty_api import _require_teacher

router = APIRouter(prefix="/api/faculty/payroll", tags=["payroll"])

COLLEGE = "School Of Engineering And Technology"
SCHEMES = ("Teaching", "Non-Teaching")
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


class PaySlipRequest(BaseModel):
    month_year: str = Field(min_length=3)
    college: str = COLLEGE
    scheme: str = "Teaching"
    employee_id: str = ""
    issued_to: str = ""
    mode: str = "payslip"  # payslip | three_month | six_month | increment


def _month_options(today: date | None = None) -> list[dict[str, str]]:
    today = today or date.today()
    options: list[dict[str, str]] = []
    year, month = today.year, today.month
    for _ in range(18):
        label = f"{MONTHS[month - 1]}-{year}"
        value = f"{year:04d}-{month:02d}"
        options.append({"value": value, "label": label})
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return options


def _parse_month(value: str) -> date:
    raw = (value or "").strip()
    try:
        year_s, month_s = raw.split("-", 1)
        year, month = int(year_s), int(month_s)
        if not (1 <= month <= 12):
            raise ValueError
        return date(year, month, 1)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Select Month / Year") from exc


def _stable_amount(seed: str, base: int, spread: int) -> float:
    total = sum(ord(ch) for ch in seed) or 1
    return float(base + (total % spread))


def _salary_for(employee_id: str, full_name: str, department: str, month: date) -> dict[str, Any]:
    seed = f"{employee_id}-{month.year}-{month.month}"
    basic = _stable_amount(seed + "basic", 42000, 9000)
    da = round(basic * 0.42, 2)
    hra = round(basic * 0.20, 2)
    conveyance = 1600.0
    medical = 1250.0
    special = _stable_amount(seed + "spl", 2500, 1500)
    pf = round(basic * 0.12, 2)
    professional_tax = 200.0
    income_tax = _stable_amount(seed + "tax", 1800, 2200)
    other_deduction = 0.0 if month.month % 3 else 500.0

    earnings = [
        {"head": "Basic Pay", "amount": basic},
        {"head": "Dearness Allowance", "amount": da},
        {"head": "House Rent Allowance", "amount": hra},
        {"head": "Conveyance Allowance", "amount": conveyance},
        {"head": "Medical Allowance", "amount": medical},
        {"head": "Special Allowance", "amount": special},
    ]
    deductions = [
        {"head": "Provident Fund", "amount": pf},
        {"head": "Professional Tax", "amount": professional_tax},
        {"head": "Income Tax", "amount": income_tax},
        {"head": "Other Deduction", "amount": other_deduction},
    ]
    gross = round(sum(item["amount"] for item in earnings), 2)
    total_deduction = round(sum(item["amount"] for item in deductions), 2)
    net = round(gross - total_deduction, 2)
    days_in_month = monthrange(month.year, month.month)[1]
    return {
        "month_label": f"{MONTHS[month.month - 1]}-{month.year}",
        "month_value": f"{month.year:04d}-{month.month:02d}",
        "employee_id": employee_id,
        "employee_name": full_name,
        "department": department or "Computer Science",
        "designation": "Assistant Professor",
        "bank_account": f"XXXXXX{(sum(ord(ch) for ch in employee_id) % 9000) + 1000}",
        "uan": f"100{(sum(ord(ch) for ch in employee_id) % 900000) + 100000}",
        "pan": f"ABCDE{(sum(ord(ch) for ch in employee_id) % 9000) + 1000}F",
        "working_days": days_in_month,
        "paid_days": days_in_month,
        "earnings": earnings,
        "deductions": deductions,
        "gross": gross,
        "total_deduction": total_deduction,
        "net": net,
        "net_in_words": _amount_in_words(net),
    }


def _amount_in_words(amount: float) -> str:
    ones = [
        "",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
        "Fifteen",
        "Sixteen",
        "Seventeen",
        "Eighteen",
        "Nineteen",
    ]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two_digits(n: int) -> str:
        if n < 20:
            return ones[n]
        return f"{tens[n // 10]} {ones[n % 10]}".strip()

    def three_digits(n: int) -> str:
        if n < 100:
            return two_digits(n)
        return f"{ones[n // 100]} Hundred {two_digits(n % 100)}".strip()

    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))
    if rupees == 0:
        words = "Zero"
    else:
        crore, rem = divmod(rupees, 10000000)
        lakh, rem = divmod(rem, 100000)
        thousand, rem = divmod(rem, 1000)
        parts = []
        if crore:
            parts.append(f"{three_digits(crore)} Crore")
        if lakh:
            parts.append(f"{three_digits(lakh)} Lakh")
        if thousand:
            parts.append(f"{three_digits(thousand)} Thousand")
        if rem:
            parts.append(three_digits(rem))
        words = " ".join(parts)
    result = f"Rupees {words} Only"
    if paise:
        result = f"Rupees {words} and {two_digits(paise)} Paise Only"
    return " ".join(result.split())


def _shift_month(month: date, offset: int) -> date:
    year = month.year
    value = month.month + offset
    while value < 1:
        value += 12
        year -= 1
    while value > 12:
        value -= 12
        year += 1
    return date(year, value, 1)


def _employee_payload(teacher: dict[str, Any]) -> dict[str, str]:
    return {
        "id": teacher["id"],
        "employee_id": teacher["employee_id"],
        "full_name": teacher["full_name"],
        "department": teacher.get("department") or "Computer Science",
        "label": f"[{teacher['employee_id']}] {teacher['full_name']}",
    }


@router.get("/meta")
def payroll_meta(access_token: str | None = Cookie(default=None)):
    teacher, _ = _require_teacher(access_token)
    employee = _employee_payload(teacher)
    return {
        "colleges": [COLLEGE],
        "schemes": list(SCHEMES),
        "months": _month_options(),
        "employees": [employee],
        "default_employee_id": employee["employee_id"],
        "default_college": COLLEGE,
        "default_scheme": "Teaching",
    }


@router.post("/payslip")
def payroll_payslip(
    body: PaySlipRequest,
    access_token: str | None = Cookie(default=None),
):
    teacher, _ = _require_teacher(access_token)
    employee = _employee_payload(teacher)
    if body.employee_id and body.employee_id != employee["employee_id"]:
        raise HTTPException(status_code=403, detail="You can view only your own payslip")
    if body.scheme not in SCHEMES:
        raise HTTPException(status_code=400, detail="Select Scheme/Staff")
    if body.college and body.college != COLLEGE:
        raise HTTPException(status_code=400, detail="Select a valid college")

    month = _parse_month(body.month_year)
    mode = (body.mode or "payslip").strip().lower()
    if mode == "three_month":
        months = [_shift_month(month, -offset) for offset in range(2, -1, -1)]
        title = "Three Month Salary Statement"
    elif mode == "six_month":
        months = [_shift_month(month, -offset) for offset in range(5, -1, -1)]
        title = "Six Month Salary Statement"
    elif mode == "increment":
        months = [month]
        title = "Increment Payslip"
    elif mode == "payslip":
        months = [month]
        title = "Employee PaySlip"
    else:
        raise HTTPException(status_code=400, detail="Unknown report mode")

    slips = [
        _salary_for(
            employee["employee_id"],
            employee["full_name"],
            employee["department"],
            item,
        )
        for item in months
    ]

    if mode == "increment":
        previous = _salary_for(
            employee["employee_id"],
            employee["full_name"],
            employee["department"],
            _shift_month(month, -1),
        )
        current_basic = slips[0]["earnings"][0]["amount"]
        # Demo increment for the selected month (stable per employee).
        increment = _stable_amount(f"{employee['employee_id']}-inc", 2500, 1500)
        previous_basic = round(current_basic - increment, 2)
        slips[0]["previous_basic"] = previous_basic
        slips[0]["increment_amount"] = increment
        slips[0]["previous_month_label"] = previous["month_label"]
        slips[0]["increment_note"] = (
            f"Increment of ₹ {increment:,.2f} applied over previous basic "
            f"₹ {previous_basic:,.2f} ({previous['month_label']})."
        )

    return {
        "title": title,
        "mode": mode,
        "college": body.college or COLLEGE,
        "scheme": body.scheme,
        "issued_to": (body.issued_to or "").strip(),
        "generated_on": date.today().strftime("%d/%m/%Y"),
        "employee": employee,
        "slips": slips,
        "summary": {
            "gross_total": round(sum(item["gross"] for item in slips), 2),
            "deduction_total": round(sum(item["total_deduction"] for item in slips), 2),
            "net_total": round(sum(item["net"] for item in slips), 2),
        },
    }


@router.get("/payslip")
def payroll_payslip_get(
    month_year: str = Query(...),
    college: str = Query(default=COLLEGE),
    scheme: str = Query(default="Teaching"),
    employee_id: str = Query(default=""),
    issued_to: str = Query(default=""),
    mode: str = Query(default="payslip"),
    access_token: str | None = Cookie(default=None),
):
    return payroll_payslip(
        PaySlipRequest(
            month_year=month_year,
            college=college,
            scheme=scheme,
            employee_id=employee_id,
            issued_to=issued_to,
            mode=mode,
        ),
        access_token=access_token,
    )
