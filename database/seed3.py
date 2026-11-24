#!/usr/bin/env python3
"""
Migrate the live erp_ai demo onto Term III registered courses.

What this script does:
- Adds subject_type / credits columns if missing.
- Renames the 10 legacy CS/MA/EC courses in place (keeps IDs, enrollments,
  assignments, and submissions).
- Inserts the 11th Term III course (DIN003A) and enrolls every student.
- Ensures each Term III course has two starter assignments.
- Is safe to re-run: already-migrated rows are left unchanged.

Run from the project environment:
    python database/seed3.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Legacy code -> Term III course payload.
# teacher_num maps to EMP00N.
COURSE_REPLACEMENTS = {
    "CS305": (
        "BCO011A",
        "Computer Networks",
        3,
        5,
        "Theory",
        4.00,
        "Network protocols, routing and communication.",
    ),
    "EC302": (
        "BCO005B",
        "Data Structure and Algorithms Lab",
        3,
        1,
        "Practical",
        1.00,
        "Lab exercises for data structures and algorithms.",
    ),
    "CS301": (
        "BCO002B",
        "Data Structures and Algorithms",
        3,
        1,
        "Theory",
        4.00,
        "Arrays, linked lists, trees, graphs and algorithms.",
    ),
    "MA302": (
        "BAS007B",
        "Discrete Mathematics",
        3,
        3,
        "Theory",
        3.00,
        "Logic, sets, relations, graphs and combinatorics.",
    ),
    "CS303": (
        "DEN003A",
        "Life Skills 1 (Personality Development)",
        3,
        2,
        "Project",
        2.00,
        "Personality development and soft skills.",
    ),
    "CS304": (
        "BCO008B",
        "Operating Systems",
        3,
        2,
        "Theory",
        3.00,
        "Processes, threads, scheduling and memory management.",
    ),
    "EC301": (
        "BCO014B",
        "Operating Systems Lab",
        3,
        2,
        "Practical",
        1.00,
        "Hands-on operating systems laboratory.",
    ),
    "CS302": (
        "BCO235B",
        "Probabilistic Modelling and Reasoning with Python",
        3,
        3,
        "Theory",
        4.00,
        "Probability models and reasoning using Python.",
    ),
    "MA301": (
        "BCO280B",
        "R Programming for Data Science and Data Analysis",
        3,
        4,
        "Theory",
        4.00,
        "Data analysis with R for data science.",
    ),
    "CS306": (
        "BCO232A",
        "Software Engineering and Project Management",
        3,
        5,
        "Theory",
        3.00,
        "Software lifecycle, requirements, testing and project management.",
    ),
}

NEW_COURSE = (
    "DIN003A",
    "Value Education 1",
    3,
    4,
    "Project",
    1.00,
    "Foundational value education.",
)

TERM_III_CODES = {payload[0] for payload in COURSE_REPLACEMENTS.values()} | {NEW_COURSE[0]}


class SeedSafetyError(RuntimeError):
    """Raised when the database cannot be migrated safely."""


def _connect() -> psycopg.Connection:
    params: dict[str, str] = {"dbname": os.getenv("PGDATABASE", "erp_ai")}
    for key, env_name in (
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("user", "PGUSER"),
        ("password", "PGPASSWORD"),
    ):
        value = os.getenv(env_name)
        if value:
            params[key] = value
    return psycopg.connect(**params)


def _teacher_id(cur, teacher_num: int) -> str:
    cur.execute(
        "SELECT id FROM teachers WHERE employee_id = %s",
        (f"EMP{teacher_num:03d}",),
    )
    row = cur.fetchone()
    if not row:
        raise SeedSafetyError(f"Missing teacher EMP{teacher_num:03d}")
    return row[0]


def _ensure_columns(cur) -> None:
    cur.execute(
        """
        ALTER TABLE courses
            ADD COLUMN IF NOT EXISTS subject_type VARCHAR(20) NOT NULL DEFAULT 'Theory',
            ADD COLUMN IF NOT EXISTS credits NUMERIC(4,2) NOT NULL DEFAULT 4.00
        """
    )
    # Postgres cannot add CHECK in the same IF NOT EXISTS ADD easily across versions;
    # enforce allowed values with a lightweight update instead of failing older DBs.
    cur.execute(
        """
        UPDATE courses
        SET subject_type = 'Theory'
        WHERE subject_type IS NULL
           OR subject_type NOT IN ('Theory', 'Practical', 'Project')
        """
    )
    cur.execute(
        """
        UPDATE courses
        SET credits = 4.00
        WHERE credits IS NULL OR credits <= 0
        """
    )


def _ensure_assignments(
    cur, course_id, teacher_id, course_name: str, subject_type: str
) -> int:
    # Practical labs do not carry LMS assignments.
    if subject_type == "Practical":
        cur.execute(
            """
            DELETE FROM submissions
            WHERE assignment_id IN (
                SELECT id FROM assignments WHERE course_id = %s
            )
            """,
            (course_id,),
        )
        cur.execute("DELETE FROM assignments WHERE course_id = %s", (course_id,))
        return 0

    cur.execute("SELECT COUNT(*) FROM assignments WHERE course_id = %s", (course_id,))
    count = cur.fetchone()[0]
    if count >= 2:
        return 0

    created = 0
    for number, days, marks in ((1, 7, 20), (2, 14, 30)):
        title = f"{course_name} - Assignment {number}"
        cur.execute(
            "SELECT 1 FROM assignments WHERE course_id = %s AND title = %s",
            (course_id, title),
        )
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO assignments
                (course_id, created_by, title, description, due_date, max_marks)
            VALUES
                (%s, %s, %s, %s, NOW() + (%s * INTERVAL '1 day'), %s)
            """,
            (
                course_id,
                teacher_id,
                title,
                f"Complete Assignment {number} for {course_name}.",
                days,
                marks,
            ),
        )
        created += 1
    return created


def main() -> int:
    print("ERP_AI Term III course migration (seed3.py)")
    print("Database: erp_ai")

    updated = 0
    unchanged = 0
    inserted = 0
    enrolled = 0
    assignments_created = 0

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                _ensure_columns(cur)

                for old_code, payload in COURSE_REPLACEMENTS.items():
                    (
                        new_code,
                        new_name,
                        semester,
                        teacher_num,
                        subject_type,
                        credits,
                        description,
                    ) = payload
                    teacher_id = _teacher_id(cur, teacher_num)

                    cur.execute(
                        """
                        SELECT id, course_code, course_name, semester, teacher_id,
                               subject_type, credits, description
                        FROM courses
                        WHERE course_code IN (%s, %s)
                        ORDER BY CASE WHEN course_code = %s THEN 0 ELSE 1 END
                        FOR UPDATE
                        """,
                        (old_code, new_code, old_code),
                    )
                    rows = cur.fetchall()

                    if not rows:
                        raise SeedSafetyError(
                            f"Neither {old_code} nor {new_code} exists. "
                            "Run the base seed first."
                        )

                    row = rows[0]
                    course_id = row[0]

                    already = (
                        row[1] == new_code
                        and row[2] == new_name
                        and int(row[3]) == semester
                        and row[5] == subject_type
                        and float(row[6]) == float(credits)
                        and (row[7] or "") == description
                    )
                    if already and str(row[4]) == str(teacher_id):
                        unchanged += 1
                        print(f"UNCHANGED {new_code}: {new_name}")
                    else:
                        # Avoid unique collisions while swapping codes.
                        if row[1] != new_code:
                            cur.execute(
                                "SELECT 1 FROM courses WHERE course_code = %s AND id <> %s",
                                (new_code, course_id),
                            )
                            if cur.fetchone():
                                raise SeedSafetyError(
                                    f"Target code {new_code} already exists on another row."
                                )

                        cur.execute(
                            """
                            UPDATE courses
                            SET course_code = %s,
                                course_name = %s,
                                semester = %s,
                                teacher_id = %s,
                                subject_type = %s,
                                credits = %s,
                                description = %s
                            WHERE id = %s
                            """,
                            (
                                new_code,
                                new_name,
                                semester,
                                teacher_id,
                                subject_type,
                                credits,
                                description,
                                course_id,
                            ),
                        )
                        updated += 1
                        print(f"UPDATED   {row[1]} -> {new_code}: {new_name}")

                    assignments_created += _ensure_assignments(
                        cur, course_id, teacher_id, new_name, subject_type
                    )

                # Insert the 11th course if needed.
                (
                    new_code,
                    new_name,
                    semester,
                    teacher_num,
                    subject_type,
                    credits,
                    description,
                ) = NEW_COURSE
                teacher_id = _teacher_id(cur, teacher_num)
                cur.execute(
                    "SELECT id FROM courses WHERE course_code = %s FOR UPDATE",
                    (new_code,),
                )
                existing = cur.fetchone()
                if existing:
                    course_id = existing[0]
                    cur.execute(
                        """
                        UPDATE courses
                        SET course_name = %s,
                            semester = %s,
                            teacher_id = %s,
                            subject_type = %s,
                            credits = %s,
                            description = %s
                        WHERE id = %s
                        """,
                        (
                            new_name,
                            semester,
                            teacher_id,
                            subject_type,
                            credits,
                            description,
                            course_id,
                        ),
                    )
                    print(f"UNCHANGED {new_code}: {new_name}")
                    unchanged += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO courses
                            (course_code, course_name, semester, teacher_id,
                             subject_type, credits, description)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            new_code,
                            new_name,
                            semester,
                            teacher_id,
                            subject_type,
                            credits,
                            description,
                        ),
                    )
                    course_id = cur.fetchone()[0]
                    inserted += 1
                    print(f"INSERTED  {new_code}: {new_name}")

                cur.execute(
                    """
                    INSERT INTO enrollments (student_id, course_id)
                    SELECT s.id, %s
                    FROM students s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM enrollments e
                        WHERE e.student_id = s.id AND e.course_id = %s
                    )
                    """,
                    (course_id, course_id),
                )
                enrolled += cur.rowcount
                assignments_created += _ensure_assignments(
                    cur, course_id, teacher_id, new_name, subject_type
                )

                # Final check: exactly the Term III catalog.
                cur.execute(
                    """
                    SELECT course_code
                    FROM courses
                    WHERE semester = 3
                    ORDER BY course_code
                    """
                )
                actual = {row[0] for row in cur.fetchall()}
                if actual != TERM_III_CODES:
                    raise SeedSafetyError(
                        f"Semester 3 catalog mismatch. actual={sorted(actual)} "
                        f"expected={sorted(TERM_III_CODES)}"
                    )

                # Drop any leftover non-Term-III semester-3 codes if somehow present.
                # (Should not happen after replacements; kept as a safety net.)
                extras = actual - TERM_III_CODES
                if extras:
                    raise SeedSafetyError(f"Unexpected semester-3 courses remain: {extras}")

        print("\nSUCCESS: Term III course migration committed.")
        print(f"Updated courses: {updated}")
        print(f"Already correct: {unchanged}")
        print(f"Inserted courses: {inserted}")
        print(f"New enrollments: {enrolled}")
        print(f"Assignments created: {assignments_created}")
        return 0

    except SeedSafetyError as exc:
        print(f"\nSAFETY CHECK FAILED: {exc}", file=sys.stderr)
        print("The transaction was rolled back; no changes were committed.", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"\nDATABASE ERROR: {exc}", file=sys.stderr)
        print("The transaction was rolled back; no changes were committed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
