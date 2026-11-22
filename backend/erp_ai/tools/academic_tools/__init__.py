"""Academic module tools (read-only)."""

from erp_ai.tools.academic_tools.attendance import (
    build_attendance_summary,
    format_attendance_summary,
    get_attendance_summary,
)
from erp_ai.tools.academic_tools.profile import (
    format_profile_summary,
    get_student_profile,
)
from erp_ai.tools.academic_tools.results import (
    format_fees_summary,
    format_results_summary,
    get_fees_summary,
    get_results_summary,
)

__all__ = [
    "build_attendance_summary",
    "format_attendance_summary",
    "format_fees_summary",
    "format_profile_summary",
    "format_results_summary",
    "get_attendance_summary",
    "get_fees_summary",
    "get_results_summary",
    "get_student_profile",
]
