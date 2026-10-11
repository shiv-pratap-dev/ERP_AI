-- ==========================================
-- ERP_AI: SYNTHETIC SEED DATA
-- Demo password for all accounts: Demo@123
-- ==========================================

BEGIN;

-- 1. USERS
-- pgcrypto generates bcrypt-compatible Blowfish hashes.
INSERT INTO users (id, email, password_hash, role)
VALUES
(
    '00000000-0000-4000-8000-000000000001',
    'admin@erp.test', crypt('Demo@123', gen_salt('bf')),
    'super_admin'
);

INSERT INTO users (id, email, password_hash, role)
SELECT
    ('10000000-0000-4000-8000-' || LPAD(n::text, 12, '0'))::UUID,
    'student' || n || '@erp.test',
    crypt('Demo@123', gen_salt('bf')),
    'student'
FROM generate_series(1, 10) AS n;

INSERT INTO users (id, email, password_hash, role)
SELECT
    ('20000000-0000-4000-8000-' || LPAD(n::text, 12, '0'))::UUID,
    'teacher' || n || '@erp.test',
    crypt('Demo@123', gen_salt('bf')),
    'teacher'
FROM generate_series(1, 5) AS n;

-- 2. STUDENTS
INSERT INTO students
    (user_id, roll_number, full_name, semester, branch)
SELECT
    ('10000000-0000-4000-8000-' || LPAD(n::text, 12, '0'))::UUID,
    'BT2026' || LPAD(n::text, 3, '0'),
    (ARRAY[
        'Aarav Sharma', 'Ananya Patel', 'Vihaan Shah',
        'Diya Mehta', 'Aditya Verma', 'Ishita Joshi',
        'Arjun Singh', 'Saanvi Desai', 'Reyansh Gupta',
        'Myra Trivedi'
    ])[n],
    3,
    'CSE'
FROM generate_series(1, 10) AS n;

-- 3. TEACHERS
INSERT INTO teachers
    (user_id, employee_id, full_name, department)
VALUES
('20000000-0000-4000-8000-000000000001',
 'EMP001', 'Dr. Neha Sharma', 'Computer Science'),
('20000000-0000-4000-8000-000000000002',
 'EMP002', 'Prof. Raj Mehta', 'Computer Science'),
('20000000-0000-4000-8000-000000000003',
 'EMP003', 'Dr. Priya Shah', 'Mathematics'),
('20000000-0000-4000-8000-000000000004',
 'EMP004', 'Prof. Karan Patel', 'Electronics'),
('20000000-0000-4000-8000-000000000005',
 'EMP005', 'Dr. Aditi Verma', 'Computer Science');

-- 4. COURSES
-- Two courses per teacher; realistic B.Tech CSE subjects.
INSERT INTO courses
    (course_code, course_name, semester, teacher_id, description)
SELECT
    v.course_code,
    v.course_name,
    v.semester,
    t.id,
    v.description
FROM (VALUES
    ('CS301', 'Data Structures and Algorithms', 3, 1,
     'Arrays, linked lists, trees, graphs and algorithms.'),
    ('CS302', 'Database Management Systems', 3, 1,
     'Relational databases, SQL, normalization and transactions.'),
    ('CS303', 'Object-Oriented Programming', 3, 2,
     'Classes, objects, inheritance and polymorphism.'),
    ('CS304', 'Operating Systems', 3, 2,
     'Processes, threads, scheduling and memory management.'),
    ('MA301', 'Engineering Mathematics III', 3, 3,
     'Linear algebra, probability and differential equations.'),
    ('MA302', 'Discrete Mathematics', 3, 3,
     'Logic, sets, relations, graphs and combinatorics.'),
    ('EC301', 'Digital Logic Design', 3, 4,
     'Boolean algebra, logic gates and sequential circuits.'),
    ('EC302', 'Computer Organization', 3, 4,
     'Processor architecture, memory and instruction execution.'),
    ('CS305', 'Computer Networks', 3, 5,
     'Network protocols, routing and communication.'),
    ('CS306', 'Software Engineering', 3, 5,
     'Software lifecycle, requirements, testing and design.')
) AS v(course_code, course_name, semester, teacher_num, description)
JOIN teachers t ON t.employee_id = 'EMP' || LPAD(v.teacher_num::text, 3, '0');

-- 5. ENROLLMENTS
-- All 10 students take all 10 courses for this demo.
INSERT INTO enrollments (student_id, course_id)
SELECT s.id, c.id
FROM students s
CROSS JOIN courses c;

-- 6. ASSIGNMENTS
-- Two assignments per course.
INSERT INTO assignments
    (course_id, created_by, title, description, due_date, max_marks)
SELECT
    c.id,
    c.teacher_id,
    a.title,
    a.description,
    NOW() + (a.days_ahead * INTERVAL '1 day'),
    a.max_marks
FROM courses c
JOIN (VALUES
    (1, 'Assignment 1: Fundamentals',
     'Explain the fundamental concepts with examples.', 7, 20::NUMERIC),
    (2, 'Assignment 2: Practical Application',
     'Solve the practical problems related to this subject.', 14, 30::NUMERIC)
) AS a(assignment_num, title, description, days_ahead, max_marks)
ON TRUE;

COMMIT;