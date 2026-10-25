
"""Assignment assistant host."""

from datetime import datetime
from typing import Any

from assignment_tools import (
    find_assignment,
    list_current_assignments,
    list_my_courses,
)
from database import get_connection
from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatRequest(BaseModel):
    message: str


def _current_student(
    access_token: str | None,
) -> tuple[dict[str, Any], str, int]:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    from main import verify_token

    user = verify_token(access_token)

    if user["role"] != "student":
        raise HTTPException(
            status_code=403,
            detail="Only students can use the assignment assistant",
        )

    with get_connection() as conn:
        student = conn.execute(
            "SELECT id, semester FROM students WHERE user_id = %s",
            (user["id"],),
        ).fetchone()

    if not student:
        raise HTTPException(
            status_code=404,
            detail="Student profile not found",
        )

    return user, str(student[0]), student[1]


def _parse_due_date(value: str) -> datetime:
    """Parse a deadline and normalize it to the server's local timezone."""
    due_date = datetime.fromisoformat(value)
    return due_date.astimezone()


def _date_label(value: str) -> str:
    return _parse_due_date(value).strftime("%d %b %Y, %I:%M %p")


def _effective_status(
    assignment: dict[str, Any],
    now: datetime,
) -> str:
    """Calculate overdue status without overriding submission statuses."""
    status = (assignment.get("status") or "pending").casefold()

    if status in ("submitted", "late"):
        return status

    try:
        due_date = _parse_due_date(assignment["due_date"])
    except (ValueError, TypeError, KeyError):
        return status

    if status == "overdue" or (
        status == "pending" and due_date <= now
    ):
        return "overdue"

    return status


def _answer(
    message: str,
    *,
    kind: str,
    title: str,
    assignments: list[dict[str, Any]] | None = None,
    courses: list[dict[str, Any]] | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "message": message,
        "kind": kind,
        "title": title,
        "assignments": assignments or [],
        "courses": courses or [],
        "action": action,
    }


@router.post("/chat")
def chat(
    data: ChatRequest,
    access_token: str | None = Cookie(default=None),
):
    _user, student_id, semester = _current_student(access_token)

    message = data.message.strip()

    if not message:
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty",
        )

    assignments = list_current_assignments(student_id, semester)

    print("DEBUG SEMESTER:", semester)
    print("DEBUG ASSIGNMENT COUNT:", len(assignments))
    print(
        "DEBUG ASSIGNMENTS:",
        [
            {
                "id": a["id"],
                "course_code": a["course_code"],
                "course_name": a["course_name"],
                "title": a["title"],
            }
            for a in assignments
        ],
    )
    normalized = message.casefold()
    
    asks_for_list = any(
        phrase in normalized
        for phrase in ("show", "list", "view", "display", "give me")
    ) and "assignment" in normalized

    asks_for_count = any(
        phrase in normalized
        for phrase in ("how many", "count", "number of", "total")
    ) and "assignment" in normalized

    asks_for_dates = any(
        phrase in normalized
        for phrase in (
            "last date",
            "latest",
            "next",
            "due date",
            "deadline",
            "due",
            "closes soon",
            "close soon",
            "closes first",
            "closing first",
            "nearest deadline",
            "upcoming deadline",
            "earliest deadline",
            "which assignment is due",
        )
    ) and (
        "assignment" in normalized
        or any(
            phrase in normalized
            for phrase in (
                "deadline",
                "due date",
                "last date",
                "closes soon",
                "close soon",
            )
        )
    )

    asks_to_upload = any(
        phrase in normalized
        for phrase in ("upload", "submit", "send")
    )

    # Upload requests require an identified assignment and user confirmation.
    if asks_to_upload:
        assignment = find_assignment(message, assignments)

        if not assignment:
            return _answer(
                "Mention one subject or assignment title so I can identify "
                "the correct upload destination.",
                kind="help",
                title="Which assignment should I use?",
            )

        return _answer(
            f"I found {assignment['title']} for "
            f"{assignment['course_code']} - {assignment['course_name']}. "
            "Attach your PDF or DOCX and confirm the upload below.",
            kind="upload",
            title="Ready for your file",
            action={
                "type": "upload_confirmation",
                "assignment": assignment,
            },
        )

    # Upcoming and latest deadline requests.
    if asks_for_dates:
        now = datetime.now().astimezone()

        if not assignments:
            return _answer(
                f"There are no assignments in your current semester "
                f"({semester}).",
                kind="empty",
                title="No assignments found",
            )

        upcoming = []

        for item in assignments:
            try:
                due_date = _parse_due_date(item["due_date"])
            except (ValueError, TypeError, KeyError):
                continue

            if (
                _effective_status(item, now) == "pending"
                and due_date > now
            ):
                upcoming.append((due_date, item))

        upcoming.sort(key=lambda pair: pair[0])

        if not upcoming:
            return _answer(
                "You have no pending assignments with upcoming deadlines.",
                kind="empty",
                title="No upcoming deadlines",
            )

        wants_latest = (
            "latest" in normalized or "last date" in normalized
        )

        selected = upcoming[-1][1] if wants_latest else upcoming[0][1]
        direction = "latest" if wants_latest else "nearest upcoming"

        return _answer(
            f"Your {direction} assignment deadline is "
            f"{_date_label(selected['due_date'])}.",
            kind="deadline",
            title=f"{direction.title()} deadline",
            assignments=[selected],
        )

    # Detect assignment status requests.
    asks_for_status = any(
        phrase in normalized
        for phrase in (
            "pending assignment",
            "pending assignments",
            "not submitted",
            "haven't submitted",
            "have not submitted",
            "yet to submit",
            "overdue",
            "past deadline",
            "past due",
            "missed deadline",
            "submitted assignment",
            "submitted assignments",
            "already submitted",
            "my submissions",
        )
    )

    # Return details for a specifically named assignment.
    if (
        "assignment" in normalized
        and not asks_for_list
        and not asks_for_count
        and not asks_for_status
    ):
        assignment = find_assignment(message, assignments)

        if assignment:
            return _answer(
                f"Here are the details for {assignment['title']}.",
                kind="assignment_list",
                title="Assignment details",
                assignments=[assignment],
            )

    # Identify the requested status filter.
    asks_for_pending = any(
        phrase in normalized
        for phrase in (
            "pending assignment",
            "pending assignments",
            "not submitted",
            "haven't submitted",
            "have not submitted",
            "yet to submit",
        )
    )

    asks_for_overdue = any(
        phrase in normalized
        for phrase in (
            "overdue",
            "past deadline",
            "past due",
            "missed deadline",
        )
    )

    asks_for_submitted = any(
        phrase in normalized
        for phrase in (
            "submitted assignment",
            "submitted assignments",
            "already submitted",
            "my submissions",
        )
    )

    # Filter assignments using calculated statuses.
    now = datetime.now().astimezone()

    if asks_for_overdue:
        filtered_assignments = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
            if _effective_status(item, now) == "overdue"
        ]
        result_title = "Overdue assignments"

    elif asks_for_pending:
        filtered_assignments = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
            if _effective_status(item, now) == "pending"
        ]
        result_title = "Pending assignments"

    elif asks_for_submitted:
        filtered_assignments = [
            {**item, "status": _effective_status(item, now)}
            for item in assignments
            if _effective_status(item, now) in ("submitted", "late")
        ]
        result_title = "Submitted assignments"

    else:
        filtered_assignments = assignments
        result_title = f"Semester {semester} assignments"

    # Return assignment lists and counts.
    if (
        asks_for_count
        or asks_for_list
        or asks_for_pending
        or asks_for_overdue
        or asks_for_submitted
        or "semester" in normalized
    ):
        count = len(filtered_assignments)

        if count == 0:
            return _answer(
                f"No {result_title.lower()} were found "
                "in your current semester.",
                kind="empty",
                title=result_title,
            )

        return _answer(
            f"Found {count} assignment(s). Here is your "
            f"{result_title.lower()} list, ordered by deadline.",
            kind="assignment_list",
            title=result_title,
            assignments=filtered_assignments,
        )

    # Course questions use structured database results.
    asks_for_courses = any(
        phrase in normalized
        for phrase in (
            "course",
            "courses",
            "subject",
            "subjects",
            "teacher",
            "teachers",
            "faculty",
            "instructor",
            "enrolled",
        )
    )

    if asks_for_courses:
        courses = list_my_courses(student_id, semester)

        if not courses:
            return _answer(
                f"No enrolled courses were found for semester {semester}.",
                kind="empty",
                title="No courses found",
            )

        return _answer(
            f"You are enrolled in {len(courses)} courses "
            f"in semester {semester}.",
            kind="course_list",
            title="Your enrolled courses",
            courses=courses,
        )

    # Delegate other questions to the LangGraph ERP agent.
    try:
        answer = invoke_erp_agent(
            question=message,
            student_id=student_id,
            semester=semester,
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=(
                "The ERP assistant is temporarily unavailable. "
                "Please try again."
            ),
        )

    return _answer(
        answer,
        kind="help",
        title="ERP Assistant",
    )
