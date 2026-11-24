"""Academic profile read helpers (shared by HTTP API + chatbot tools)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from database import get_connection
from erp_ai.tools.academic_tools.attendance import build_attendance_summary


def _roman(num: int) -> str:
    mapping = {
        1: "I",
        2: "II",
        3: "III",
        4: "IV",
        5: "V",
        6: "VI",
        7: "VII",
        8: "VIII",
    }
    return mapping.get(int(num), str(num))


def get_student_profile(student_id: str) -> dict[str, Any]:
    """Return the full read-only academic profile for a student id."""
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
            WHERE s.id = %s
            """,
            (student_id,),
        ).fetchone()

        if not row:
            raise ValueError("Student profile not found")

        _id, roll_number, full_name, semester, branch, email, created_at = row

        courses = conn.execute(
            """
            SELECT
                c.course_code,
                c.course_name,
                c.semester,
                c.description,
                t.full_name AS teacher_name,
                COALESCE(c.subject_type, 'Theory') AS subject_type,
                COALESCE(c.credits, 4.0) AS credits
            FROM enrollments e
            JOIN courses c ON c.id = e.course_id
            LEFT JOIN teachers t ON t.id = c.teacher_id
            WHERE e.student_id = %s
            ORDER BY c.course_code
            """,
            (student_id,),
        ).fetchall()

    # Admission batch 2025-2029. Current term is semester 3.
    session_label = "Jul 2026 - Dec 2026 (3rd Semester)"
    degree = (
        "Bachelor of Technology - Computer Science and Engineering"
        if branch == "CSE"
        else f"Bachelor of Technology - {branch}"
    )

    return {
        "id": str(_id),
        "rrn": roll_number,
        "enrollment_no": roll_number,
        "full_name": full_name,
        "email": email,
        "mobile": "9876543210",
        "login_status": "Enabled",
        "admission_status": "Admitted",
        "admission_batch": "2025-2029",
        "admission_type": "REGULAR",
        "school": "School of Engineering and Technology",
        "degree_branch": degree,
        "semester": semester,
        "branch": branch,
        "scheme": f"B.Tech-{branch}-2025-2029-Department of Computer Engineering (New)",
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
                "receipt_no": "C/TF/25/1001",
                "receipt_date": "15/07/2025",
                "applied": 120000.0,
                "paid": 120000.0,
                "outstanding": 0.0,
            },
            {
                "year": "Second Year",
                "semester": "III",
                "receipt_type": "Academic Fees",
                "receipt_no": "C/TF/26/2104",
                "receipt_date": "12/07/2026",
                "applied": 110000.0,
                "paid": 110000.0,
                "outstanding": 0.0,
            },
        ],
        "available_sessions": [
            "Jul 2025 - Dec 2025 (1st Semester)",
            "Jan 2026 - Jun 2026 (2nd Semester)",
            "Jul 2026 - Dec 2026 (3rd Semester)",
        ],
        "attendance_summary": build_attendance_summary(
            str(_id),
            semester=int(semester),
            session_label=session_label,
        ),
        "results": [
            {
                "semester": "I",
                "session": "Jul 2025 - Dec 2025 (1st Semester)",
                "section": "SF",
                "registered_courses": 8,
                "failed_courses": 0,
                "registered_credits": 22.0,
                "earned_credits": 22.0,
                "sgpa": 7.80,
                "result_date": "20 Jan 2026",
            },
            {
                "semester": "II",
                "session": "Jan 2026 - Jun 2026 (2nd Semester)",
                "section": "SF",
                "registered_courses": 9,
                "failed_courses": 0,
                "registered_credits": 24.0,
                "earned_credits": 24.0,
                "sgpa": 7.65,
                "result_date": "18 Jun 2026",
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
                "subject_type": c[5],
                "credits": float(c[6]),
                "course_registered": True,
                "exam_registered": True,
            }
            for c in courses
        ],
    }

