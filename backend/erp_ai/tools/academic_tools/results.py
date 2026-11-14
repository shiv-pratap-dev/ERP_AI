"""Academic results / fees read helpers."""

from __future__ import annotations

from typing import Any

from erp_ai.tools.academic_tools.profile import get_student_profile


def get_results_summary(student_id: str) -> dict[str, Any]:
    """Return semester results for the student (read-only)."""
    profile = get_student_profile(student_id)
    return {
        "full_name": profile.get("full_name"),
        "semester": profile.get("semester"),
        "branch": profile.get("branch"),
        "results": profile.get("results") or [],
        "internal_marks": profile.get("internal_marks") or [],
    }


def get_fees_summary(student_id: str) -> dict[str, Any]:
    """Return academic fee ledger summary (read-only)."""
    profile = get_student_profile(student_id)
    fees = profile.get("fees") or []
    outstanding = sum(float(row.get("outstanding") or 0) for row in fees)
    return {
        "full_name": profile.get("full_name"),
        "semester": profile.get("semester"),
        "branch": profile.get("branch"),
        "fees": fees,
        "total_outstanding": outstanding,
    }


def format_results_summary(payload: dict[str, Any]) -> str:
    """Markdown summary for chatbot answers."""
    lines = [
        "### Result details\n",
        f"- **Student:** {payload.get('full_name')}",
        "",
        "#### Semester results",
    ]
    results = payload.get("results") or []
    if not results:
        lines.append("- No results available.")
    else:
        for row in results:
            lines.append(
                f"- **Sem {row.get('semester')}** ({row.get('session')}): "
                f"SGPA **{row.get('sgpa')}**, "
                f"credits {row.get('earned_credits')}/{row.get('registered_credits')}, "
                f"failed {row.get('failed_courses')}"
            )
    return "\n".join(lines) + "\n"


def format_fees_summary(payload: dict[str, Any]) -> str:
    """Markdown summary for chatbot answers."""
    lines = [
        "### Fees details\n",
        f"- **Student:** {payload.get('full_name')}",
        f"- **Total outstanding:** ₹{payload.get('total_outstanding')}",
        "",
        "#### Fee ledger",
    ]
    fees = payload.get("fees") or []
    if not fees:
        lines.append("- No fee rows available.")
    else:
        for row in fees:
            lines.append(
                f"- **{row.get('year')} / Sem {row.get('semester')}** "
                f"({row.get('receipt_type')}): "
                f"paid ₹{row.get('paid')} / applied ₹{row.get('applied')}, "
                f"outstanding ₹{row.get('outstanding')} "
                f"[{row.get('receipt_no')}]"
            )
    return "\n".join(lines) + "\n"
