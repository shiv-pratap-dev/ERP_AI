-- ==========================================
-- ERP_AI: DATABASE SCHEMA
-- PostgreSQL 18+
-- ==========================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. USERS: Authentication for every role
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role VARCHAR(20) NOT NULL
        CHECK (role IN ('super_admin', 'teacher', 'student')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. STUDENTS: Student-specific information
CREATE TABLE students (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    roll_number VARCHAR(30) UNIQUE NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    semester SMALLINT CHECK (semester BETWEEN 1 AND 8),
    branch VARCHAR(50) NOT NULL DEFAULT 'CSE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 3. TEACHERS: Teacher-specific information
CREATE TABLE teachers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID UNIQUE NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    employee_id VARCHAR(30) UNIQUE NOT NULL,
    full_name VARCHAR(150) NOT NULL,
    department VARCHAR(100) NOT NULL DEFAULT 'Computer Science',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. COURSES: Subjects taught by teachers
CREATE TABLE courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_code VARCHAR(20) UNIQUE NOT NULL,
    course_name VARCHAR(150) NOT NULL,
    semester SMALLINT NOT NULL CHECK (semester BETWEEN 1 AND 8),
    teacher_id UUID NOT NULL
        REFERENCES teachers(id) ON DELETE RESTRICT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. ENROLLMENTS: Students enrolled in courses
CREATE TABLE enrollments (
    student_id UUID NOT NULL
        REFERENCES students(id) ON DELETE CASCADE,
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (student_id, course_id)
);

-- 6. ASSIGNMENTS: Assignments created by teachers
CREATE TABLE assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    created_by UUID NOT NULL
        REFERENCES teachers(id) ON DELETE RESTRICT,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    due_date TIMESTAMPTZ NOT NULL,
    max_marks NUMERIC(6,2) NOT NULL DEFAULT 100
        CHECK (max_marks > 0),
    allowed_file_types TEXT[] NOT NULL
        DEFAULT ARRAY['pdf', 'docx', 'zip'],
    max_file_size_mb INTEGER NOT NULL DEFAULT 10
        CHECK (max_file_size_mb > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 7. SUBMISSIONS: Student assignment uploads
CREATE TABLE submissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assignment_id UUID NOT NULL
        REFERENCES assignments(id) ON DELETE CASCADE,
    student_id UUID NOT NULL
        REFERENCES students(id) ON DELETE CASCADE,
    file_name VARCHAR(255) NOT NULL,
    file_url TEXT NOT NULL,
    file_size_bytes BIGINT CHECK (file_size_bytes > 0),
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) NOT NULL DEFAULT 'submitted'
        CHECK (status IN ('submitted', 'late')),
    marks_obtained NUMERIC(6,2)
        CHECK (marks_obtained >= 0),
    feedback TEXT,

    -- One current submission per student per assignment
    UNIQUE (assignment_id, student_id)
);

-- ==========================================
-- INDEXES
-- ==========================================

CREATE INDEX idx_students_user_id
    ON students(user_id);

CREATE INDEX idx_teachers_user_id
    ON teachers(user_id);

CREATE INDEX idx_courses_teacher_id
    ON courses(teacher_id);

CREATE INDEX idx_assignments_course_id
    ON assignments(course_id);

CREATE INDEX idx_assignments_created_by
    ON assignments(created_by);

CREATE INDEX idx_assignments_due_date
    ON assignments(due_date);

CREATE INDEX idx_submissions_student_id
    ON submissions(student_id);

CREATE INDEX idx_submissions_assignment_id
    ON submissions(assignment_id);

CREATE INDEX idx_users_role
    ON users(role);

-- 8. TEACHING PLAN: faculty lecture plan (Academic → Teaching Plan Entry)
CREATE TABLE teaching_plan_topics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    unit_no SMALLINT NOT NULL CHECK (unit_no BETWEEN 1 AND 6),
    topic_no SMALLINT NOT NULL CHECK (topic_no > 0),
    topic_title TEXT NOT NULL,
    UNIQUE (course_id, unit_no, topic_no)
);

CREATE TABLE teaching_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL
        REFERENCES teachers(id) ON DELETE CASCADE,
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    plan_type VARCHAR(20) NOT NULL DEFAULT 'core'
        CHECK (plan_type IN ('core', 'elective')),
    session_label VARCHAR(40) NOT NULL,
    college_label VARCHAR(250) NOT NULL,
    teaching_for VARCHAR(30) NOT NULL DEFAULT 'Theory',
    section VARCHAR(10) NOT NULL,
    unit_no SMALLINT NOT NULL CHECK (unit_no BETWEEN 1 AND 6),
    topic_no SMALLINT NOT NULL CHECK (topic_no > 0),
    topic_description TEXT NOT NULL,
    sessions_required SMALLINT NOT NULL CHECK (sessions_required > 0),
    timetable_range VARCHAR(40) NOT NULL,
    proposed_date DATE,
    conduct_date DATE,
    lecture_slot VARCHAR(40),
    proposed_dates JSONB NOT NULL DEFAULT '{}'::jsonb,
    lecture_slots JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_teaching_plans_teacher
    ON teaching_plans(teacher_id, plan_type, proposed_date);

CREATE INDEX idx_teaching_plan_topics_course
    ON teaching_plan_topics(course_id, unit_no, topic_no);

-- 9. ATTENDANCE BY FACULTY: lecture calendar and present/absent marks
CREATE TABLE faculty_class_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL
        REFERENCES teachers(id) ON DELETE CASCADE,
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    session_date DATE NOT NULL,
    slot_label VARCHAR(40) NOT NULL,
    slot_name VARCHAR(40) NOT NULL,
    slot_sort SMALLINT NOT NULL DEFAULT 1,
    section VARCHAR(10) NOT NULL DEFAULT 'S',
    course_type VARCHAR(20) NOT NULL DEFAULT 'CORE',
    lecture_type VARCHAR(20) NOT NULL DEFAULT 'REGULAR',
    class_type VARCHAR(40) NOT NULL DEFAULT 'Regular Class',
    attendance_status VARCHAR(20) NOT NULL DEFAULT 'Pending',
    topic_unit SMALLINT,
    topic_no SMALLINT,
    topic_title TEXT,
    topic_covered_status VARCHAR(20) NOT NULL DEFAULT 'Not Started',
    locked BOOLEAN NOT NULL DEFAULT FALSE,
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (teacher_id, course_id, session_date, slot_label)
);

CREATE TABLE faculty_attendance_marks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL
        REFERENCES faculty_class_sessions(id) ON DELETE CASCADE,
    student_id UUID NOT NULL
        REFERENCES students(id) ON DELETE CASCADE,
    mark VARCHAR(10) NOT NULL DEFAULT 'P'
        CHECK (mark IN ('P', 'A', 'OD')),
    late_time VARCHAR(8) NOT NULL DEFAULT '',
    disciplinary BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (session_id, student_id)
);

CREATE INDEX idx_faculty_sessions_teacher_date
    ON faculty_class_sessions(teacher_id, session_date);

CREATE INDEX idx_faculty_marks_session
    ON faculty_attendance_marks(session_id);

-- 10. EXTRA CLASS TIME TABLE
CREATE TABLE faculty_extra_classes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL
        REFERENCES teachers(id) ON DELETE CASCADE,
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    sr_no INTEGER NOT NULL,
    plan_type VARCHAR(40) NOT NULL,
    session_label VARCHAR(40) NOT NULL,
    course_details VARCHAR(250) NOT NULL,
    college_label VARCHAR(250) NOT NULL,
    semester SMALLINT NOT NULL,
    teaching_for VARCHAR(30) NOT NULL,
    section VARCHAR(10) NOT NULL,
    batch VARCHAR(20) NOT NULL DEFAULT '',
    slot_type VARCHAR(40) NOT NULL,
    slot_label VARCHAR(80) NOT NULL,
    room VARCHAR(80) NOT NULL DEFAULT '',
    regular_class BOOLEAN NOT NULL DEFAULT FALSE,
    start_date DATE,
    end_date DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_faculty_extra_classes_teacher
    ON faculty_extra_classes(teacher_id, status, sr_no);

-- 11. EXAMINATION MARK ENTRY
CREATE TABLE faculty_exam_sheets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    teacher_id UUID NOT NULL
        REFERENCES teachers(id) ON DELETE CASCADE,
    course_id UUID NOT NULL
        REFERENCES courses(id) ON DELETE CASCADE,
    session_label VARCHAR(40) NOT NULL,
    exam_name VARCHAR(40) NOT NULL,
    section VARCHAR(10) NOT NULL,
    locked BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (teacher_id, course_id, session_label, exam_name, section)
);

CREATE TABLE faculty_exam_marks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sheet_id UUID NOT NULL
        REFERENCES faculty_exam_sheets(id) ON DELETE CASCADE,
    student_id UUID NOT NULL
        REFERENCES students(id) ON DELETE CASCADE,
    marks_obtained NUMERIC(6,2),
    status_code VARCHAR(10),
    UNIQUE (sheet_id, student_id)
);

CREATE INDEX idx_faculty_exam_sheets_teacher
    ON faculty_exam_sheets(teacher_id, course_id, exam_name);

-- ==========================================
-- END OF SCHEMA
-- ==========================================