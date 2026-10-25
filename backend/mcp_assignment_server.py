"""MCP-facing assignment tool definitions.

This module is intentionally transport-independent for now. It gives the
future MCP server one stable assignment tool surface while the ERP chat host
continues to enforce authentication and confirmation.
"""

from assignment_tools import find_assignment, list_current_assignments

ASSIGNMENT_TOOLS = (
    "list_current_assignments",
    "find_assignment",
    "get_next_deadline",
    "request_upload_confirmation",
)

__all__ = ["ASSIGNMENT_TOOLS", "find_assignment", "list_current_assignments"]
