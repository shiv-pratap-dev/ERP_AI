"""Academic module tools (read-only pure JSON)."""

from erp_ai.tools.academic_tools.attendance import (
    build_attendance_summary,
    filter_attendance_subjects,
    get_attendance_summary,
)
from erp_ai.tools.academic_tools.profile import (
    get_student_profile,
)
from erp_ai.tools.academic_tools.results import (
    get_fees_summary,
    get_results_summary,
)

__all__ = [
    "build_attendance_summary",
    "filter_attendance_subjects",
    "get_attendance_summary",
    "get_fees_summary",
    "get_results_summary",
    "get_student_profile",
]

