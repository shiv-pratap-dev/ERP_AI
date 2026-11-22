"""Instant grounded plans from live ERP data (no model round-trip)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from erp_ai.tools.academic_tools.attendance import get_attendance_summary
from erp_ai.tools.lms_tools.assignment_tools import list_current_assignments


def _status(row: dict[str, Any], now: datetime) -> str:
    status = str(row.get("status") or "pending").casefold()
    if status in {"submitted", "late"}:
        return status
    due_raw = row.get("due_date")
    if not due_raw:
        return status
    try:
        due = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00"))
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
        if status == "pending" and due <= now:
            return "overdue"
    except ValueError:
        return status
    return status


def build_student_focus_plan(student_id: str, semester: int) -> str:
    """Build a short study/focus plan from attendance + assignment status."""
    summary = get_attendance_summary(student_id)
    assignments = list_current_assignments(student_id, semester)
    now = datetime.now().astimezone()
    tagged = [{**row, "status": _status(row, now)} for row in assignments]

    subjects = list(summary.get("subjects") or [])
    subjects_sorted = sorted(
        subjects,
        key=lambda row: (
            float(row.get("percent") or 0),
            str(row.get("code") or ""),
        ),
    )
    weak = [row for row in subjects_sorted if float(row.get("percent") or 0) < 90][:5]
    if not weak:
        weak = subjects_sorted[:3]

    overdue = [row for row in tagged if row["status"] == "overdue"]
    overdue = sorted(overdue, key=lambda row: str(row.get("due_date") or ""))[:6]
    pending = [
        row
        for row in tagged
        if row["status"] == "pending"
    ]
    pending = sorted(pending, key=lambda row: str(row.get("due_date") or ""))[:6]

    lines = [
        f"### Focus plan for semester {semester}",
        "",
        f"Overall attendance: **{summary.get('percent', 0)}%** "
        f"({summary.get('present', 0)}/{summary.get('total_classes', 0)}).",
        "",
        "**1. Weak attendance subjects**",
    ]
    if weak:
        for row in weak:
            lectures = row.get("lectures") or f"{row.get('present')}/{row.get('total')}"
            lines.append(
                f"- **{row.get('code')}** {row.get('name')}: "
                f"{row.get('percent')}% ({lectures})"
            )
    else:
        lines.append("- No subject attendance rows yet.")

    lines.extend(["", "**2. Overdue / pending assignments**"])
    focus_rows = (overdue + pending)[:8]
    if focus_rows:
        for row in focus_rows:
            lines.append(
                f"- **{row.get('course_code')}** {row.get('title')} "
                f"(due {row.get('due_date')}, status: {row.get('status')})"
            )
    else:
        lines.append("- No pending assignments right now.")

    lines.extend(
        [
            "",
            "**3. Suggested order this week**",
            "- Day 1–2: recover the lowest-attendance subjects above.",
            "- Day 3–4: submit the earliest pending/overdue assignments.",
            "- Day 5: review remaining pending work and check LMS uploads.",
            "",
            "Open **LMS → Transactions** for submissions and "
            "**Academic → Student Complete Detail** for attendance.",
        ]
    )
    return "\n".join(lines)


def build_faculty_focus_plan(
    teacher: dict[str, Any],
    courses: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> str:
    """Build a short faculty day plan from courses + unchecked work."""
    pending = [row for row in assignments if int(row.get("unchecked") or 0) > 0]
    pending = sorted(
        pending,
        key=lambda row: (-int(row.get("unchecked") or 0), str(row.get("course_code") or "")),
    )

    lines = [
        f"### Focus plan for {teacher.get('full_name') or 'Faculty'}",
        "",
        f"You teach **{len(courses)}** course(s).",
        "",
        "**1. Grading first**",
    ]
    if pending:
        for row in pending[:6]:
            lines.append(
                f"- **{row.get('course_code')}** {row.get('title')}: "
                f"**{row.get('unchecked')} unchecked** "
                f"({row.get('replies')} replies, due {row.get('due_date')})"
            )
    else:
        lines.append("- No unchecked submissions right now.")

    lines.extend(["", "**2. Course load**"])
    if courses:
        for row in courses[:6]:
            lines.append(
                f"- **{row.get('course_code')}** {row.get('course_name')}: "
                f"{row.get('student_count')} students, "
                f"{row.get('assignment_count')} assignments"
            )
    else:
        lines.append("- No courses assigned.")

    lines.extend(
        [
            "",
            "**3. Suggested day order**",
            "- Morning: clear unchecked submissions (LMS → Check Student Assignment).",
            "- Midday: prep for courses with the most students.",
            "- Afternoon: attendance / mark entry if a class session is due.",
            "",
            "Ask 'pending checks', 'my courses', or 'leave balance' for the raw lists.",
        ]
    )
    return "\n".join(lines)
