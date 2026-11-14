"""Academic attendance read helpers."""

from __future__ import annotations

from typing import Any

from erp_ai.tools.academic_tools.profile import get_student_profile


def get_attendance_summary(student_id: str) -> dict[str, Any]:
    """Return attendance summary for the student (read-only)."""
    profile = get_student_profile(student_id)
    summary = profile.get("attendance_summary") or {}
    return {
        "full_name": profile.get("full_name"),
        "semester": profile.get("semester"),
        "branch": profile.get("branch"),
        **summary,
    }


def format_attendance_summary(summary: dict[str, Any]) -> str:
    """Markdown summary for chatbot answers."""
    lines = [
        "### Attendance summary\n",
        f"- **Student:** {summary.get('full_name')}",
        f"- **Session:** {summary.get('session')}",
        f"- **Total classes:** {summary.get('total_classes')}",
        f"- **Present:** {summary.get('present')}",
        f"- **Absent:** {summary.get('absent')}",
        f"- **OD:** {summary.get('od')}",
        f"- **Overall %:** {summary.get('percent')}",
        "",
        "#### Subject-wise",
    ]
    subjects = summary.get("subjects") or []
    if not subjects:
        lines.append("- No subject attendance rows available.")
    else:
        for item in subjects:
            lines.append(
                f"- **{item.get('code')}** {item.get('name')}: "
                f"{item.get('present')}/{item.get('total')} "
                f"({item.get('percent')}%)"
            )
    return "\n".join(lines) + "\n"
