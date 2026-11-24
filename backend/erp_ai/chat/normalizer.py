"""Shared text normalization, typo fixes, course aliases, and slot helpers.

Replaces the 3 previously duplicated copies of course-alias / typo / date /
assignment-number logic in `assistant.py`, `faculty_assistant.py`,
`assignment_tools.py`, and `attendance.py`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))

MONTHS: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

# Unified canonical course aliases across Student LMS, Academic Attendance,
# Examination, and Faculty LMS modules.
COURSE_ALIASES: dict[str, tuple[str, ...]] = {
    "dsa lab": (
        "data structure and algorithms lab",
        "data structures and algorithms lab",
        "dsa lab",
    ),
    "dsa": (
        "data structures and algorithms",
        "data structure and algorithms",
    ),
    "os lab": (
        "operating systems lab",
        "os lab",
    ),
    "os": ("operating systems",),
    "cn": ("computer networks",),
    "networks": ("computer networks",),
    "dbms": (
        "database management systems",
        "database",
    ),
    "oop": ("object oriented programming",),
    "se": (
        "software engineering",
        "software engineering and project management",
    ),
    "software engineering": (
        "software engineering",
        "software engineering and project management",
    ),
    "spm": ("software engineering and project management",),
    "discrete": ("discrete mathematics",),
    "dm": ("discrete mathematics",),
    "math": (
        "discrete mathematics",
        "engineering mathematics",
    ),
    "maths": (
        "discrete mathematics",
        "engineering mathematics",
    ),
    "python": (
        "probabilistic modelling",
        "probabilistic modelling and reasoning with python",
    ),
    "probabilistic": ("probabilistic modelling",),
    "r programming": (
        "r programming",
        "r programming for data science",
    ),
    "data science": ("r programming for data science",),
    "life skills": (
        "life skills",
        "personality development",
    ),
    "personality development": (
        "personality development",
        "life skills",
    ),
    "value education": ("value education",),
}

# Whole-word typo corrections only (never prefix replacements that could corrupt
# valid words).
_TYPO_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (r"\battendence\b", "attendance"),
        (r"\batendance\b", "attendance"),
        (r"\battandance\b", "attendance"),
        (r"\bassigment\b", "assignment"),
        (r"\bassingment\b", "assignment"),
        (r"\basignments\b", "assignments"),
        (r"\bassigments\b", "assignments"),
        (r"\bassingments\b", "assignments"),
        (r"\bsemister\b", "semester"),
        (r"\bstaus\b", "status"),
        (r"\btimetabel\b", "timetable"),
        (r"\btime\s+tabel\b", "time table"),
        (r"\badmitcard\b", "admit card"),
        (r"\bhallticket\b", "hall ticket"),
        (r"\bpaysilp\b", "payslip"),
        (r"\bholydays\b", "holidays"),
        (r"\bsubmited\b", "submitted"),
        (r"\bpendng\b", "pending"),
    )
)

_COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{2,4}\d{3}[A-Za-z]?)\b")
_ASSIGNMENT_NUM_RE = re.compile(
    r"\b(?:assignment|assign|asgn|hw|homework|task)\s*(?:no\.?|number|#)?\s*([0-9]{1,2})\b"
)
_HASH_NUM_RE = re.compile(r"(?:^|\s)#([0-9]{1,2})\b")
_WHITESPACE_RE = re.compile(r"\s+")
_ALNUM_TOKENS_RE = re.compile(r"[^a-z0-9]+")


def normalize_chat_text(text: str) -> str:
    """Lowercase, collapse whitespace, and fix common whole-word ERP typos."""
    normalized = _WHITESPACE_RE.sub(" ", (text or "").casefold()).strip()
    if not normalized:
        return ""
    for compiled, replacement in _TYPO_PATTERNS:
        normalized = compiled.sub(replacement, normalized)
    return normalized


def normalize_alnum(text: str) -> str:
    """Normalize text to space-separated lowercase alphanumeric tokens."""
    return _ALNUM_TOKENS_RE.sub(" ", (text or "").casefold()).strip()


def has_phrase(text: str, phrase: str) -> bool:
    """Match a phrase with word boundaries (e.g. `submit` does not hit `submitted`)."""
    if not text or not phrase:
        return False
    return re.search(r"\b" + re.escape(phrase.casefold()) + r"\b", text) is not None


def has_any_phrase(text: str, phrases: tuple[str, ...] | list[str]) -> bool:
    """Return True if any phrase matches with word boundaries."""
    return any(has_phrase(text, phrase) for phrase in phrases)


def extract_course_code_hint(text: str) -> str | None:
    """Extract an explicit ERP course code (e.g. `BCO002B`, `DIN003A`, `DMA002A`)."""
    if not text:
        return None
    for match in _COURSE_CODE_RE.finditer(text):
        candidate = match.group(1).upper()
        # Exclude common non-course tokens like Sem1 / Day1 if any slip in
        if candidate.startswith(("SEM", "DAY", "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")):
            continue
        return candidate
    return None


def extract_assignment_number(text: str) -> int | None:
    """Extract an explicitly requested assignment number (e.g. `assignment 1`, `hw 2`)."""
    normalized = normalize_chat_text(text)
    if not normalized:
        return None
    match = _ASSIGNMENT_NUM_RE.search(normalized)
    if match:
        return int(match.group(1))
    if has_any_phrase(normalized, ("assignment", "assignments", "upload", "submit", "homework", "hw")):
        hash_match = _HASH_NUM_RE.search(normalized)
        if hash_match:
            return int(hash_match.group(1))
    return None


def extract_assignment_title(message: str) -> str | None:
    """Extract an explicit custom assignment title from a faculty create command."""
    if not message:
        return None
    patterns = (
        r"(?:titled|title|named|called)\s+[\"']?"
        r"([^\"'\n]+?)[\"']?"
        r"(?=\s+(?:due|deadline|on\s+\d|by\s+\d|,|\.|$))",
        r"assignment\s+(?:on|about|for)\s+[\"']?"
        r"([^\"'\n]+?)[\"']?"
        r"(?=\s+(?:due|deadline|in\s+|on\s+\d|by\s+\d|,|\.|$))",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            title = match.group(1).strip(" .,-")
            if len(title) >= 3 and "assignment" not in title.casefold():
                return title[:200]
    return None


def expand_course_aliases(query: str) -> list[str]:
    """Return normalized query plus canonical course-name expansions for any aliases."""
    clean = normalize_alnum(normalize_chat_text(query))
    if not clean:
        return []
    words = set(clean.split())
    terms: list[str] = [clean]
    for alias, expansions in COURSE_ALIASES.items():
        if alias in words or has_phrase(clean, alias):
            for exp in expansions:
                if exp not in terms:
                    terms.append(exp)
    return terms


def match_course_from_list(
    query: str,
    courses: list[dict[str, Any]],
    *,
    code_key: str = "course_code",
    name_key: str = "course_name",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Score and match a course dict from `courses` by code, alias, or name tokens.

    Returns `(best_single_match_or_None, top_candidates)`.
    Respects `"lab"` vs theory distinction so `"DSA"` and `"DSA lab"` resolve cleanly.
    """
    normalized = normalize_chat_text(query)
    alnum = normalize_alnum(normalized)
    if not alnum or not courses:
        return None, []

    compact = alnum.replace(" ", "")
    wants_lab = has_phrase(alnum, "lab")
    scored: list[tuple[int, dict[str, Any]]] = []

    for course in courses:
        raw_code = str(course.get(code_key) or course.get("code") or "")
        raw_name = str(course.get(name_key) or course.get("name") or "")
        code = normalize_alnum(raw_code)
        name = normalize_alnum(raw_name)
        score = 0

        if code and (has_phrase(alnum, code) or code.replace(" ", "") in compact):
            score += 120

        is_lab = has_phrase(name, "lab")
        if wants_lab and is_lab:
            score += 25
        elif wants_lab and not is_lab:
            score -= 15
        elif not wants_lab and is_lab:
            score -= 10

        for alias, needles in COURSE_ALIASES.items():
            if not has_phrase(alnum, alias):
                continue
            alias_is_lab = alias.endswith("lab")
            for needle in needles:
                if needle in name:
                    if alias_is_lab == is_lab:
                        score += 55
                    else:
                        score += 25
            if alias in name:
                score += 35

        if name and name in alnum:
            score += 70

        for token in name.split():
            if len(token) < 4 or token in {"data", "with", "and", "for"}:
                continue
            if has_phrase(alnum, token):
                score += 10

        if score > 0:
            scored.append((score, course))

    if not scored:
        return None, []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score = scored[0][0]
    top = [course for score, course in scored if score == best_score]
    if len(top) == 1 and best_score >= 20:
        return top[0], top
    return None, [course for _score, course in scored[:5]]


def parse_dates(message: str, *, reference_date: date | None = None) -> list[date]:
    """Extract calendar dates like `27 Nov`, `27/11/2026`, or `2026-11-27`."""
    text = (message or "").casefold()
    found: list[date] = []
    today = reference_date or date.today()

    # ISO YYYY-MM-DD first so 2026-11-27 is parsed unambiguously
    for match in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        year, month, day = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        try:
            found.append(date(year, month, day))
        except ValueError:
            continue

    # DD Mon [YYYY] (e.g. 27 Nov, 27th November 2026)
    for match in re.finditer(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s*[-/ ]\s*"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)"
        r"(?:\s*[, ]\s*(\d{4}))?\b",
        text,
    ):
        day = int(match.group(1))
        month_key = match.group(2).casefold()
        month = MONTHS.get(month_key) or MONTHS.get(month_key[:3])
        if not month:
            continue
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            found.append(date(year, month, day))
        except ValueError:
            continue

    # Mon DD[, YYYY] (e.g. Nov 27, November 27 2026)
    for match in re.finditer(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?"
        r"(?:\s*[, ]\s*(\d{4}))?\b",
        text,
    ):
        month_key = match.group(1).casefold()
        month = MONTHS.get(month_key) or MONTHS.get(month_key[:3])
        day = int(match.group(2))
        if not month:
            continue
        year = int(match.group(3)) if match.group(3) else today.year
        try:
            found.append(date(year, month, day))
        except ValueError:
            continue

    # DD/MM/YYYY or DD-MM-YY
    for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text):
        first, second, third = (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )
        if first > 1900:
            continue
        year = third + 2000 if third < 100 else third
        try:
            found.append(date(year, second, first))
        except ValueError:
            continue

    unique: list[date] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique


def parse_iso_or_natural_date(value: str) -> date | None:
    """Parse an ISO date/datetime string or natural date phrase into a `date`."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(IST).date()
    except ValueError:
        pass
    parsed = parse_dates(raw)
    return parsed[0] if parsed else None


def parse_datetime_ist(value: Any) -> datetime | None:
    """Parse a datetime or ISO string and convert it to IST."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def format_ist_datetime(value: Any) -> str:
    """Format a datetime or ISO string as a clean human-readable IST string."""
    dt = parse_datetime_ist(value)
    if dt is None:
        return str(value or "")
    return dt.strftime("%d %b %Y, %I:%M %p IST")


def compute_effective_assignment_status(
    raw_status: str | None,
    due_date: Any,
    now: datetime | None = None,
) -> str:
    """Compute canonical assignment status (`pending` -> `overdue` when past due)."""
    status = (raw_status or "pending").strip().casefold()
    if status in {"submitted", "late"}:
        return status
    due_dt = parse_datetime_ist(due_date)
    if due_dt is None:
        return status
    ref = (now or datetime.now(IST)).astimezone(IST)
    if status == "overdue" or (status == "pending" and due_dt <= ref):
        return "overdue"
    return status
