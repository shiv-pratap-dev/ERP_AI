#!/usr/bin/env python3
"""
Safely update the existing ERP demo assignments in the `erp_ai` PostgreSQL DB.

What this script does:
- Updates existing assignment rows in place (does NOT delete/reinsert them).
- Preserves assignment IDs, submissions, due dates, marks, and all other tables.
- Renames the two generic assignments for each expected course.
- Is safe to run again: already-updated rows are left unchanged.
- Uses a single transaction; unexpected data causes a rollback.

Run from the project environment:
    python seed2.py
"""

import sys
import psycopg


DATABASE = "dbname=erp_ai"

# The expected course codes and course-specific assignment descriptions.
# Titles are generated as "<course name> - Assignment 1/2".
COURSE_ASSIGNMENTS = {
    "CS301": {
        1: "Implement and analyze core data structures and sorting/searching algorithms.",
        2: "Solve problems using trees, graphs, and algorithmic complexity analysis.",
    },
    "CS302": {
        1: "Design relational tables and write SQL queries using filtering, joins, and aggregation.",
        2: "Apply normalization, constraints, transactions, and integrity rules to a database.",
    },
    "CS303": {
        1: "Create classes and objects demonstrating encapsulation, constructors, and methods.",
        2: "Implement inheritance, polymorphism, abstraction, and interfaces in a small program.",
    },
    "CS304": {
        1: "Explain process states and compare CPU scheduling algorithms with examples.",
        2: "Analyze synchronization, deadlocks, paging, and virtual memory scenarios.",
    },
    "MA301": {
        1: "Solve problems involving matrices, eigenvalues, and systems of linear equations.",
        2: "Apply probability and differential-equation techniques to engineering problems.",
    },
    "MA302": {
        1: "Solve exercises involving propositional logic, predicates, sets, and relations.",
        2: "Apply graph theory, counting principles, and combinatorics to given problems.",
    },
    "EC301": {
        1: "Simplify Boolean expressions and design combinational logic circuits.",
        2: "Design and analyze flip-flops, counters, and sequential logic circuits.",
    },
    "EC302": {
        1: "Trace instruction execution and explain CPU datapath and control-unit behavior.",
        2: "Analyze cache organization, memory hierarchy, and input/output mechanisms.",
    },
    "CS305": {
        1: "Explain the OSI/TCP-IP models and analyze common application and transport protocols.",
        2: "Work through IP addressing, subnetting, routing, and network troubleshooting problems.",
    },
    "CS306": {
        1: "Produce software requirements and UML models for a small application.",
        2: "Prepare a testing plan and apply software testing and quality-assurance techniques.",
    },
}

OLD_TITLE_TO_NUMBER = {
    "Assignment 1: Fundamentals": 1,
    "Assignment 2: Practical Application": 2,
}


class SeedSafetyError(RuntimeError):
    """Raised when the current database does not match expected safe-update conditions."""


def main() -> int:
    print("ERP_AI assignment update (seed2.py)")
    print("Database: erp_ai")
    print("This script updates existing rows only; it does not delete assignments.")

    updated = 0
    unchanged = 0

    try:
        # The connection context commits if everything succeeds and rolls back
        # automatically if an exception escapes the block.
        with psycopg.connect(DATABASE) as conn:
            with conn.cursor() as cur:
                expected_codes = tuple(COURSE_ASSIGNMENTS.keys())

                cur.execute(
                    """
                    SELECT id, course_code, course_name, teacher_id
                    FROM courses
                    WHERE course_code = ANY(%s)
                    ORDER BY course_code
                    """,
                    (list(expected_codes),),
                )
                course_rows = cur.fetchall()
                courses_by_code = {row[1]: row for row in course_rows}

                missing_codes = sorted(set(expected_codes) - set(courses_by_code))
                if missing_codes:
                    raise SeedSafetyError(
                        "Missing expected course(s): " + ", ".join(missing_codes)
                        + ". No changes have been committed."
                    )

                if len(course_rows) != len(expected_codes):
                    raise SeedSafetyError(
                        "Unexpected duplicate course codes were found. No changes committed."
                    )

                # Check for pre-existing duplicate target titles within a course
                # before updating anything.
                for code in expected_codes:
                    course_id, course_code, course_name, _teacher_id = courses_by_code[code]
                    cur.execute(
                        """
                        SELECT id, title, description, created_at, due_date, max_marks
                        FROM assignments
                        WHERE course_id = %s
                        ORDER BY created_at NULLS FIRST, id
                        FOR UPDATE
                        """,
                        (course_id,),
                    )
                    rows = cur.fetchall()

                    if len(rows) != 2:
                        raise SeedSafetyError(
                            f"{code} ({course_name}) has {len(rows)} assignment(s); "
                            "expected exactly 2. Nothing was committed."
                        )

                    # Determine which existing row is Assignment 1 or 2. This
                    # supports both original seed titles and titles from a prior run.
                    number_to_row = {}
                    for row in rows:
                        assignment_id, title = row[0], row[1]
                        if title in OLD_TITLE_TO_NUMBER:
                            number = OLD_TITLE_TO_NUMBER[title]
                        elif title == f"{course_name} - Assignment 1":
                            number = 1
                        elif title == f"{course_name} - Assignment 2":
                            number = 2
                        else:
                            raise SeedSafetyError(
                                f"{code} has an unexpected assignment title {title!r}. "
                                "To avoid overwriting custom data, nothing was committed."
                            )

                        if number in number_to_row:
                            raise SeedSafetyError(
                                f"{code} has duplicate Assignment {number} rows. "
                                "Nothing was committed."
                            )
                        number_to_row[number] = row

                    if set(number_to_row) != {1, 2}:
                        raise SeedSafetyError(
                            f"{code} must have one Assignment 1 and one Assignment 2. "
                            "Nothing was committed."
                        )

                    # Ensure the target titles aren't already used by another
                    # assignment in this same course (outside the two rows selected).
                    target_titles = [
                        f"{course_name} - Assignment 1",
                        f"{course_name} - Assignment 2",
                    ]
                    cur.execute(
                        """
                        SELECT id, title
                        FROM assignments
                        WHERE course_id = %s
                          AND title = ANY(%s)
                        """,
                        (course_id, target_titles),
                    )
                    target_rows = cur.fetchall()
                    target_ids = {r[0] for r in target_rows}
                    selected_ids = {number_to_row[1][0], number_to_row[2][0]}
                    if not target_ids.issubset(selected_ids):
                        raise SeedSafetyError(
                            f"{code} already has a conflicting target title on another row. "
                            "Nothing was committed."
                        )

                    for number in (1, 2):
                        row = number_to_row[number]
                        assignment_id, current_title = row[0], row[1]
                        new_title = f"{course_name} - Assignment {number}"
                        new_description = COURSE_ASSIGNMENTS[code][number]

                        # Check submissions for reporting; never delete or modify them.
                        cur.execute(
                            "SELECT COUNT(*) FROM submissions WHERE assignment_id = %s",
                            (assignment_id,),
                        )
                        submission_count = cur.fetchone()[0]

                        if current_title == new_title and row[2] == new_description:
                            unchanged += 1
                            print(
                                f"UNCHANGED {code}: {new_title} "
                                f"(submissions preserved: {submission_count})"
                            )
                            continue

                        cur.execute(
                            """
                            UPDATE assignments
                            SET title = %s,
                                description = %s
                            WHERE id = %s
                            """,
                            (new_title, new_description, assignment_id),
                        )
                        if cur.rowcount != 1:
                            raise SeedSafetyError(
                                f"Could not update assignment {assignment_id}; "
                                "nothing was committed."
                            )

                        updated += 1
                        print(
                            f"UPDATED   {code}: {current_title!r} -> {new_title!r} "
                            f"(submissions preserved: {submission_count})"
                        )

                # Final checks inside the same transaction.
                cur.execute(
                    """
                    SELECT c.course_code, COUNT(a.id)
                    FROM courses c
                    LEFT JOIN assignments a ON a.course_id = c.id
                    WHERE c.course_code = ANY(%s)
                    GROUP BY c.course_code
                    ORDER BY c.course_code
                    """,
                    (list(expected_codes),),
                )
                counts = cur.fetchall()
                bad_counts = [(code, count) for code, count in counts if count != 2]
                if bad_counts:
                    raise SeedSafetyError(
                        f"Final verification failed: {bad_counts}. "
                        "Transaction will be rolled back."
                    )

                # Verify each course now has exactly the intended two titles.
                for code in expected_codes:
                    course_id, _, course_name, _ = courses_by_code[code]
                    cur.execute(
                        """
                        SELECT title
                        FROM assignments
                        WHERE course_id = %s
                        ORDER BY title
                        """,
                        (course_id,),
                    )
                    actual = {r[0] for r in cur.fetchall()}
                    expected = {
                        f"{course_name} - Assignment 1",
                        f"{course_name} - Assignment 2",
                    }
                    if actual != expected:
                        raise SeedSafetyError(
                            f"Final title verification failed for {code}: {actual}. "
                            "Transaction will be rolled back."
                        )

        # Reaching here means the connection context committed successfully.
        print("\nSUCCESS: assignment update committed.")
        print(f"Updated: {updated}")
        print(f"Already correct: {unchanged}")
        print("Verified: 2 course-specific assignments per expected course.")
        print("Preserved: assignment IDs, due dates, marks, submissions, and other tables.")
        return 0

    except SeedSafetyError as exc:
        print(f"\nSAFETY CHECK FAILED: {exc}", file=sys.stderr)
        print("The transaction was rolled back; no changes were committed.", file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        print(f"\nDATABASE ERROR: {exc}", file=sys.stderr)
        print("The transaction was rolled back; no changes were committed.", file=sys.stderr)
        print(
            "Check that PostgreSQL is running, the `erp_ai` database exists, "
            "and psycopg is installed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
