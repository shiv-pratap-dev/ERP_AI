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

-- ==========================================
-- END OF SCHEMA
-- ==========================================