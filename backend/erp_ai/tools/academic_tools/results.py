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

