-- 教务系统第二阶段 · MySQL 8.0.16+ / InnoDB
-- 通过 mysql 命令行导入一个已创建的空数据库，见 docs/02-系统设计说明书.md。
-- DDL 覆盖静态约束。带锁的业务事务、数据范围及跨表校验见设计说明书。
SET NAMES utf8mb4;

CREATE TABLE department (
    department_code VARCHAR(10) PRIMARY KEY,
    department_name VARCHAR(100) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE major (
    major_code VARCHAR(16) PRIMARY KEY,
    department_code VARCHAR(10) NOT NULL,
    major_name VARCHAR(100) NOT NULL,
    national_code VARCHAR(16),
    national_name VARCHAR(100),
    study_years TINYINT UNSIGNED NOT NULL CHECK (study_years > 0),
    FOREIGN KEY (department_code) REFERENCES department (department_code)
) ENGINE=InnoDB;

CREATE TABLE admin_class (
    class_no CHAR(10) PRIMARY KEY,
    major_code VARCHAR(16) NOT NULL,
    entry_year SMALLINT UNSIGNED NOT NULL,
    class_name VARCHAR(100) NOT NULL,
    KEY ix_class_major_year (major_code, entry_year),
    FOREIGN KEY (major_code) REFERENCES major (major_code)
) ENGINE=InnoDB;

CREATE TABLE term (
    term_code CHAR(11) PRIMARY KEY,
    starts_on DATE NOT NULL,
    week_count TINYINT UNSIGNED NOT NULL CHECK (week_count BETWEEN 1 AND 30),
    is_current BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_current IN (0, 1)),
    current_guard TINYINT GENERATED ALWAYS AS (IF(is_current, 1, NULL)) VIRTUAL,
    UNIQUE KEY uq_current_term (current_guard)
) ENGINE=InnoDB;

CREATE TABLE campus (
    campus_code VARCHAR(10) PRIMARY KEY,
    campus_name VARCHAR(60) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE period_time (
    campus_code VARCHAR(10) NOT NULL,
    period_no TINYINT UNSIGNED NOT NULL CHECK (period_no BETWEEN 1 AND 14),
    starts_at TIME NOT NULL,
    ends_at TIME NOT NULL,
    PRIMARY KEY (campus_code, period_no),
    FOREIGN KEY (campus_code) REFERENCES campus (campus_code),
    CHECK (starts_at >= '00:00:00' AND ends_at < '24:00:00' AND ends_at > starts_at)
) ENGINE=InnoDB;

CREATE TABLE teacher (
    teacher_no VARCHAR(20) PRIMARY KEY,
    teacher_name VARCHAR(50) NOT NULL,
    department_code VARCHAR(10) NOT NULL,
    job_title VARCHAR(40),
    is_active BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_active IN (0, 1)),
    source_system VARCHAR(32) NOT NULL,
    synced_at DATETIME(3) NOT NULL,
    FOREIGN KEY (department_code) REFERENCES department (department_code)
) ENGINE=InnoDB;

CREATE TABLE classroom (
    room_code VARCHAR(24) PRIMARY KEY,
    campus_code VARCHAR(10) NOT NULL,
    building VARCHAR(60) NOT NULL,
    room_name VARCHAR(40) NOT NULL,
    seat_count SMALLINT UNSIGNED NOT NULL,
    exam_capacity SMALLINT UNSIGNED NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_active IN (0, 1)),
    source_system VARCHAR(32) NOT NULL,
    synced_at DATETIME(3) NOT NULL,
    FOREIGN KEY (campus_code) REFERENCES campus (campus_code),
    CHECK (seat_count > 0 AND exam_capacity <= seat_count)
) ENGINE=InnoDB;

CREATE TABLE import_batch (
    batch_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_system VARCHAR(32) NOT NULL,
    source_batch_no VARCHAR(64) NOT NULL,
    content_sha256 BINARY(32) NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    state ENUM('PENDING','IMPORTED','FAILED') NOT NULL DEFAULT 'PENDING',
    UNIQUE KEY uq_import_source (source_system, source_batch_no)
) ENGINE=InnoDB;

CREATE TABLE student (
    student_no CHAR(10) PRIMARY KEY,
    class_no CHAR(10) NOT NULL,
    batch_id BIGINT UNSIGNED NOT NULL,
    student_name VARCHAR(50) NOT NULL,
    gender VARCHAR(16),
    birth_date DATE,
    id_document_type VARCHAR(16),
    id_document_cipher VARBINARY(512),
    ethnicity_code CHAR(2),
    native_place VARCHAR(100),
    political_status_code CHAR(2),
    origin_region VARCHAR(60),
    admission_no VARCHAR(32),
    admitted_on DATE NOT NULL,
    expected_graduation_on DATE,
    phone_cipher VARBINARY(512),
    email VARCHAR(254),
    address VARCHAR(250),
    name_pinyin VARCHAR(100),
    academic_state ENUM('ACTIVE','SUSPENDED','WITHDRAWN','GRADUATED','COMPLETED')
        NOT NULL DEFAULT 'ACTIVE',
    KEY ix_student_class_state (class_no, academic_state),
    FOREIGN KEY (class_no) REFERENCES admin_class (class_no),
    FOREIGN KEY (batch_id) REFERENCES import_batch (batch_id)
) ENGINE=InnoDB;

CREATE TABLE family_member (
    student_no CHAR(10) NOT NULL,
    member_no SMALLINT UNSIGNED NOT NULL,
    member_name VARCHAR(50) NOT NULL,
    relationship_name VARCHAR(24) NOT NULL,
    id_document_type VARCHAR(16),
    id_document_cipher VARBINARY(512),
    phone_cipher VARBINARY(512),
    is_employed BOOLEAN CHECK (is_employed IN (0, 1)),
    PRIMARY KEY (student_no, member_no),
    FOREIGN KEY (student_no) REFERENCES student (student_no)
) ENGINE=InnoDB;

CREATE TABLE user_account (
    account_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    login_name VARCHAR(64) COLLATE utf8mb4_0900_as_cs NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    student_no CHAR(10),
    teacher_no VARCHAR(20),
    account_state ENUM('ACTIVE','LOCKED','DISABLED') NOT NULL DEFAULT 'ACTIVE',
    last_login_at DATETIME(3),
    UNIQUE KEY uq_account_login (login_name),
    UNIQUE KEY uq_account_student (student_no),
    UNIQUE KEY uq_account_teacher (teacher_no),
    FOREIGN KEY (student_no) REFERENCES student (student_no),
    FOREIGN KEY (teacher_no) REFERENCES teacher (teacher_no),
    CHECK (student_no IS NULL OR teacher_no IS NULL)
) ENGINE=InnoDB;

CREATE TABLE role (
    role_code VARCHAR(24) PRIMARY KEY,
    role_name VARCHAR(40) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE permission (
    permission_code VARCHAR(64) PRIMARY KEY,
    permission_name VARCHAR(80) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE user_role (
    account_id BIGINT UNSIGNED NOT NULL,
    role_code VARCHAR(24) NOT NULL,
    PRIMARY KEY (account_id, role_code),
    FOREIGN KEY (account_id) REFERENCES user_account (account_id),
    FOREIGN KEY (role_code) REFERENCES role (role_code)
) ENGINE=InnoDB;

CREATE TABLE role_permission (
    role_code VARCHAR(24) NOT NULL,
    permission_code VARCHAR(64) NOT NULL,
    PRIMARY KEY (role_code, permission_code),
    FOREIGN KEY (role_code) REFERENCES role (role_code),
    FOREIGN KEY (permission_code) REFERENCES permission (permission_code)
) ENGINE=InnoDB;

CREATE TABLE operation_log (
    log_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    actor_id BIGINT UNSIGNED NOT NULL,
    action_code VARCHAR(48) NOT NULL,
    target_kind VARCHAR(32) NOT NULL,
    target_ref VARCHAR(96) NOT NULL,
    result_code VARCHAR(24) NOT NULL,
    acted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_operation_actor_time (actor_id, acted_at),
    KEY ix_operation_action_time (action_code, acted_at),
    FOREIGN KEY (actor_id) REFERENCES user_account (account_id)
) ENGINE=InnoDB;

CREATE TABLE status_change (
    change_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    student_no CHAR(10) NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    change_type VARCHAR(24) NOT NULL,
    effective_on DATE NOT NULL,
    old_class_no CHAR(10) NOT NULL,
    new_class_no CHAR(10) NOT NULL,
    old_state ENUM('ACTIVE','SUSPENDED','WITHDRAWN','GRADUATED','COMPLETED') NOT NULL,
    new_state ENUM('ACTIVE','SUSPENDED','WITHDRAWN','GRADUATED','COMPLETED') NOT NULL,
    reason VARCHAR(500) NOT NULL,
    acted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_status_student_date (student_no, effective_on, change_id),
    FOREIGN KEY (student_no) REFERENCES student (student_no),
    FOREIGN KEY (actor_id) REFERENCES user_account (account_id),
    FOREIGN KEY (old_class_no) REFERENCES admin_class (class_no),
    FOREIGN KEY (new_class_no) REFERENCES admin_class (class_no)
) ENGINE=InnoDB;

CREATE TABLE course (
    course_code VARCHAR(20) PRIMARY KEY,
    course_name VARCHAR(120) NOT NULL,
    department_code VARCHAR(10) NOT NULL,
    credits DECIMAL(4,2) NOT NULL CHECK (credits >= 0),
    lecture_hours SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    practice_hours SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    seminar_hours SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    lab_hours SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    outside_hours SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    assessment ENUM('EXAM','ASSESSMENT') NOT NULL,
    course_nature VARCHAR(40) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_active IN (0, 1)),
    FOREIGN KEY (department_code) REFERENCES department (department_code)
) ENGINE=InnoDB;

CREATE TABLE program (
    program_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    major_code VARCHAR(16) NOT NULL,
    entry_year SMALLINT UNSIGNED NOT NULL,
    required_credits DECIMAL(6,2) NOT NULL CHECK (required_credits > 0),
    UNIQUE KEY uq_program_cohort (major_code, entry_year),
    FOREIGN KEY (major_code) REFERENCES major (major_code)
) ENGINE=InnoDB;

CREATE TABLE program_course (
    program_id BIGINT UNSIGNED NOT NULL,
    course_code VARCHAR(20) NOT NULL,
    curriculum_group VARCHAR(40) NOT NULL,
    course_attribute ENUM('REQUIRED','ELECTIVE','FREE_ELECTIVE') NOT NULL,
    suggested_term TINYINT UNSIGNED NOT NULL CHECK (suggested_term > 0),
    PRIMARY KEY (program_id, course_code),
    FOREIGN KEY (program_id) REFERENCES program (program_id),
    FOREIGN KEY (course_code) REFERENCES course (course_code)
) ENGINE=InnoDB;

CREATE TABLE section (
    section_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    offering_no VARCHAR(32) NOT NULL,
    course_code VARCHAR(20) NOT NULL,
    term_code CHAR(11) NOT NULL,
    section_no VARCHAR(10) NOT NULL,
    campus_code VARCHAR(10) NOT NULL,
    offered_credits DECIMAL(4,2) NOT NULL CHECK (offered_credits >= 0),
    offered_hours INT UNSIGNED NOT NULL,
    offered_nature VARCHAR(40) NOT NULL,
    offered_assessment ENUM('EXAM','ASSESSMENT') NOT NULL,
    capacity SMALLINT UNSIGNED NOT NULL CHECK (capacity > 0),
    enrolled_count SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    state ENUM('DRAFT','SCHEDULED','OPEN','LOCKED','COMPLETED','CANCELLED')
        NOT NULL DEFAULT 'DRAFT',
    UNIQUE KEY uq_section_offering (offering_no),
    UNIQUE KEY uq_section_term_course_no (term_code, course_code, section_no),
    KEY ix_section_term_state (term_code, state),
    FOREIGN KEY (course_code) REFERENCES course (course_code),
    FOREIGN KEY (term_code) REFERENCES term (term_code),
    FOREIGN KEY (campus_code) REFERENCES campus (campus_code),
    CHECK (enrolled_count <= capacity)
) ENGINE=InnoDB;

CREATE TABLE section_teacher (
    section_id BIGINT UNSIGNED NOT NULL,
    teacher_no VARCHAR(20) NOT NULL,
    duty ENUM('LEAD','ASSISTANT') NOT NULL,
    PRIMARY KEY (section_id, teacher_no),
    KEY ix_section_teacher_lookup (teacher_no, section_id),
    FOREIGN KEY (section_id) REFERENCES section (section_id),
    FOREIGN KEY (teacher_no) REFERENCES teacher (teacher_no)
) ENGINE=InnoDB;

CREATE TABLE section_class (
    section_id BIGINT UNSIGNED NOT NULL,
    class_no CHAR(10) NOT NULL,
    PRIMARY KEY (section_id, class_no),
    FOREIGN KEY (section_id) REFERENCES section (section_id),
    FOREIGN KEY (class_no) REFERENCES admin_class (class_no)
) ENGINE=InnoDB;

CREATE TABLE schedule (
    schedule_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    section_id BIGINT UNSIGNED NOT NULL,
    room_code VARCHAR(24) NOT NULL,
    day_of_week TINYINT UNSIGNED NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    first_period TINYINT UNSIGNED NOT NULL,
    last_period TINYINT UNSIGNED NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE CHECK (active IN (0, 1)),
    KEY ix_schedule_section_active (section_id, active),
    KEY ix_schedule_room_day (room_code, day_of_week, active, first_period),
    FOREIGN KEY (section_id) REFERENCES section (section_id),
    FOREIGN KEY (room_code) REFERENCES classroom (room_code),
    CHECK (first_period BETWEEN 1 AND 14 AND last_period BETWEEN first_period AND 14)
) ENGINE=InnoDB;

CREATE TABLE schedule_week (
    schedule_id BIGINT UNSIGNED NOT NULL,
    week_no TINYINT UNSIGNED NOT NULL CHECK (week_no BETWEEN 1 AND 30),
    PRIMARY KEY (schedule_id, week_no),
    FOREIGN KEY (schedule_id) REFERENCES schedule (schedule_id)
) ENGINE=InnoDB;

CREATE TABLE schedule_teacher (
    schedule_id BIGINT UNSIGNED NOT NULL,
    teacher_no VARCHAR(20) NOT NULL,
    PRIMARY KEY (schedule_id, teacher_no),
    KEY ix_schedule_teacher_lookup (teacher_no, schedule_id),
    FOREIGN KEY (schedule_id) REFERENCES schedule (schedule_id),
    FOREIGN KEY (teacher_no) REFERENCES teacher (teacher_no)
) ENGINE=InnoDB;

CREATE TABLE selection_round (
    round_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    term_code CHAR(11) NOT NULL,
    round_name VARCHAR(80) NOT NULL,
    mode ENUM('LOTTERY','DIRECT') NOT NULL,
    starts_at DATETIME(3) NOT NULL,
    ends_at DATETIME(3) NOT NULL,
    credit_limit DECIMAL(5,2) CHECK (credit_limit > 0),
    state ENUM('DRAFT','OPEN','SETTLING','CLOSED') NOT NULL DEFAULT 'DRAFT',
    lottery_seed BINARY(32),
    UNIQUE KEY uq_round_name (term_code, round_name),
    KEY ix_round_window (term_code, state, starts_at, ends_at),
    FOREIGN KEY (term_code) REFERENCES term (term_code),
    CHECK (ends_at > starts_at)
) ENGINE=InnoDB;

CREATE TABLE round_class (
    round_id BIGINT UNSIGNED NOT NULL,
    class_no CHAR(10) NOT NULL,
    PRIMARY KEY (round_id, class_no),
    FOREIGN KEY (round_id) REFERENCES selection_round (round_id),
    FOREIGN KEY (class_no) REFERENCES admin_class (class_no)
) ENGINE=InnoDB;

CREATE TABLE round_section (
    round_id BIGINT UNSIGNED NOT NULL,
    section_id BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (round_id, section_id),
    FOREIGN KEY (round_id) REFERENCES selection_round (round_id),
    FOREIGN KEY (section_id) REFERENCES section (section_id)
) ENGINE=InnoDB;

CREATE TABLE enrollment (
    enrollment_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    section_id BIGINT UNSIGNED NOT NULL,
    student_no CHAR(10) NOT NULL,
    round_id BIGINT UNSIGNED NOT NULL,
    study_type ENUM('FIRST','RETAKE','MAKEUP_STUDY') NOT NULL DEFAULT 'FIRST',
    is_exempt BOOLEAN NOT NULL DEFAULT FALSE CHECK (is_exempt IN (0, 1)),
    state ENUM('PENDING','ENROLLED','REJECTED','DROPPED','COMPLETED') NOT NULL,
    selected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_enroll_student_section (student_no, section_id),
    KEY ix_enroll_section_state_student (section_id, state, student_no),
    KEY ix_enroll_round_state (round_id, state, section_id, enrollment_id),
    FOREIGN KEY (student_no) REFERENCES student (student_no),
    FOREIGN KEY (round_id, section_id) REFERENCES round_section (round_id, section_id)
) ENGINE=InnoDB;

CREATE TABLE enroll_log (
    log_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    enrollment_id BIGINT UNSIGNED NOT NULL,
    round_id BIGINT UNSIGNED NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    old_state ENUM('PENDING','ENROLLED','REJECTED','DROPPED','COMPLETED'),
    new_state ENUM('PENDING','ENROLLED','REJECTED','DROPPED','COMPLETED') NOT NULL,
    action_type ENUM('SELECT','DROP','WIN','LOSE','ADJUST','COMPLETE') NOT NULL,
    reason VARCHAR(250),
    acted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_enroll_log_time (enrollment_id, acted_at, log_id),
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id),
    FOREIGN KEY (round_id) REFERENCES selection_round (round_id),
    FOREIGN KEY (actor_id) REFERENCES user_account (account_id)
) ENGINE=InnoDB;

CREATE TABLE exam (
    exam_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    section_id BIGINT UNSIGNED NOT NULL,
    scheduled_term_code CHAR(11) NOT NULL,
    exam_period ENUM('BEGINNING','MIDTERM','FINAL') NOT NULL,
    attempt_kind ENUM('REGULAR','MAKEUP','DEFERRED') NOT NULL,
    attempt_no TINYINT UNSIGNED NOT NULL DEFAULT 1 CHECK (attempt_no > 0),
    starts_at DATETIME(3) NOT NULL,
    ends_at DATETIME(3) NOT NULL,
    exam_method VARCHAR(24) NOT NULL,
    exam_platform VARCHAR(120),
    state ENUM('DRAFT','PUBLISHED','CANCELLED') NOT NULL DEFAULT 'DRAFT',
    UNIQUE KEY uq_exam_attempt (section_id, attempt_kind, attempt_no),
    KEY ix_exam_term_visibility (scheduled_term_code, state, exam_period, starts_at),
    KEY ix_exam_time (state, starts_at, ends_at),
    FOREIGN KEY (section_id) REFERENCES section (section_id),
    FOREIGN KEY (scheduled_term_code) REFERENCES term (term_code),
    CHECK (ends_at > starts_at)
) ENGINE=InnoDB;

CREATE TABLE exam_room (
    exam_id BIGINT UNSIGNED NOT NULL,
    room_code VARCHAR(24) NOT NULL,
    capacity_snapshot SMALLINT UNSIGNED NOT NULL CHECK (capacity_snapshot > 0),
    PRIMARY KEY (exam_id, room_code),
    KEY ix_exam_room_lookup (room_code, exam_id),
    FOREIGN KEY (exam_id) REFERENCES exam (exam_id),
    FOREIGN KEY (room_code) REFERENCES classroom (room_code)
) ENGINE=InnoDB;

CREATE TABLE exam_seat (
    exam_seat_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    exam_id BIGINT UNSIGNED NOT NULL,
    enrollment_id BIGINT UNSIGNED NOT NULL,
    room_code VARCHAR(24) NOT NULL,
    seat_no SMALLINT UNSIGNED NOT NULL CHECK (seat_no > 0),
    ticket_no VARCHAR(32) NOT NULL,
    candidate_state ENUM('SCHEDULED','DEFERRED') NOT NULL DEFAULT 'SCHEDULED',
    UNIQUE KEY uq_exam_enrollment (exam_id, enrollment_id),
    UNIQUE KEY uq_exam_seat_no (exam_id, room_code, seat_no),
    UNIQUE KEY uq_exam_ticket (ticket_no),
    KEY ix_exam_candidate (enrollment_id, candidate_state, exam_id),
    FOREIGN KEY (exam_id, room_code) REFERENCES exam_room (exam_id, room_code),
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id)
) ENGINE=InnoDB;

CREATE TABLE invigilation (
    exam_id BIGINT UNSIGNED NOT NULL,
    room_code VARCHAR(24) NOT NULL,
    teacher_no VARCHAR(20) NOT NULL,
    duty ENUM('LEAD','INVIGILATOR') NOT NULL,
    PRIMARY KEY (exam_id, room_code, teacher_no),
    UNIQUE KEY uq_exam_invigilator (exam_id, teacher_no),
    KEY ix_invigilation_teacher (teacher_no, exam_id),
    FOREIGN KEY (exam_id, room_code) REFERENCES exam_room (exam_id, room_code),
    FOREIGN KEY (teacher_no) REFERENCES teacher (teacher_no)
) ENGINE=InnoDB;

CREATE TABLE grade_scale (
    scale_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    scale_code VARCHAR(20) NOT NULL,
    version_no SMALLINT UNSIGNED NOT NULL CHECK (version_no > 0),
    scale_name VARCHAR(60) NOT NULL,
    value_kind ENUM('NUMERIC','LEVEL') NOT NULL,
    state ENUM('DRAFT','PUBLISHED','RETIRED') NOT NULL DEFAULT 'DRAFT',
    UNIQUE KEY uq_grade_scale_version (scale_code, version_no)
) ENGINE=InnoDB;

CREATE TABLE gpa_rule (
    rule_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    scale_id BIGINT UNSIGNED NOT NULL,
    min_score DECIMAL(5,2),
    max_score DECIMAL(5,2),
    level_code VARCHAR(12),
    comparable_score DECIMAL(5,2),
    gpa DECIMAL(3,2) NOT NULL CHECK (gpa BETWEEN 0 AND 4),
    passed BOOLEAN NOT NULL CHECK (passed IN (0, 1)),
    UNIQUE KEY uq_gpa_rule_level (scale_id, level_code),
    UNIQUE KEY uq_gpa_rule_range_start (scale_id, min_score),
    FOREIGN KEY (scale_id) REFERENCES grade_scale (scale_id),
    CHECK ((level_code IS NOT NULL AND min_score IS NULL AND max_score IS NULL
            AND comparable_score IS NOT NULL AND comparable_score BETWEEN 0 AND 100)
        OR (level_code IS NULL AND min_score IS NOT NULL AND max_score IS NOT NULL
            AND min_score BETWEEN 0 AND 100 AND max_score BETWEEN min_score AND 100
            AND comparable_score IS NULL))
) ENGINE=InnoDB;

CREATE TABLE grade_input_window (
    window_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    term_code CHAR(11) NOT NULL,
    attempt_kind ENUM('REGULAR','MAKEUP','DEFERRED') NOT NULL,
    opens_at DATETIME(3) NOT NULL,
    closes_at DATETIME(3) NOT NULL,
    UNIQUE KEY uq_grade_window (term_code, attempt_kind),
    FOREIGN KEY (term_code) REFERENCES term (term_code),
    CHECK (closes_at > opens_at)
) ENGINE=InnoDB;

CREATE TABLE grade_sheet (
    sheet_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    section_id BIGINT UNSIGNED NOT NULL,
    window_id BIGINT UNSIGNED NOT NULL,
    exam_id BIGINT UNSIGNED,
    attempt_no TINYINT UNSIGNED NOT NULL DEFAULT 1 CHECK (attempt_no > 0),
    scale_id BIGINT UNSIGNED,
    state ENUM('EMPTY','DRAFT','SUBMITTED','PUBLISHED') NOT NULL DEFAULT 'EMPTY',
    submitted_at DATETIME(3),
    published_at DATETIME(3),
    UNIQUE KEY uq_grade_sheet_window (section_id, window_id, attempt_no),
    UNIQUE KEY uq_grade_sheet_exam (exam_id),
    KEY ix_grade_sheet_review (state, submitted_at, sheet_id),
    FOREIGN KEY (section_id) REFERENCES section (section_id),
    FOREIGN KEY (window_id) REFERENCES grade_input_window (window_id),
    FOREIGN KEY (exam_id) REFERENCES exam (exam_id),
    FOREIGN KEY (scale_id) REFERENCES grade_scale (scale_id),
    CHECK ((state IN ('SUBMITTED','PUBLISHED') AND scale_id IS NOT NULL AND submitted_at IS NOT NULL)
        OR (state IN ('EMPTY','DRAFT') AND submitted_at IS NULL)),
    CHECK ((state = 'PUBLISHED' AND published_at IS NOT NULL AND published_at >= submitted_at)
        OR (state <> 'PUBLISHED' AND published_at IS NULL))
) ENGINE=InnoDB;

CREATE TABLE grade_component (
    component_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sheet_id BIGINT UNSIGNED NOT NULL,
    component_name VARCHAR(40) NOT NULL,
    weight_percent DECIMAL(5,2) NOT NULL CHECK (weight_percent > 0 AND weight_percent <= 100),
    order_no SMALLINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_component_name (sheet_id, component_name),
    UNIQUE KEY uq_component_order (sheet_id, order_no),
    FOREIGN KEY (sheet_id) REFERENCES grade_sheet (sheet_id)
) ENGINE=InnoDB;

CREATE TABLE grade (
    grade_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sheet_id BIGINT UNSIGNED NOT NULL,
    enrollment_id BIGINT UNSIGNED NOT NULL,
    total_score DECIMAL(5,2) CHECK (total_score BETWEEN 0 AND 100),
    level_code VARCHAR(12),
    grade_flag ENUM('NORMAL','ABSENT','DEFERRED','VIOLATION','CHEATING','INELIGIBLE')
        NOT NULL DEFAULT 'NORMAL',
    rule_id BIGINT UNSIGNED,
    gpa_snapshot DECIMAL(3,2) CHECK (gpa_snapshot BETWEEN 0 AND 4),
    credits_snapshot DECIMAL(4,2) NOT NULL CHECK (credits_snapshot >= 0),
    hours_snapshot INT UNSIGNED NOT NULL,
    nature_snapshot VARCHAR(40) NOT NULL,
    version_no INT UNSIGNED NOT NULL DEFAULT 1 CHECK (version_no > 0),
    UNIQUE KEY uq_grade_sheet_enrollment (sheet_id, enrollment_id),
    KEY ix_grade_enroll_history (enrollment_id, sheet_id),
    FOREIGN KEY (sheet_id) REFERENCES grade_sheet (sheet_id),
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id),
    FOREIGN KEY (rule_id) REFERENCES gpa_rule (rule_id),
    CHECK (total_score IS NULL OR level_code IS NULL),
    CHECK ((rule_id IS NULL AND gpa_snapshot IS NULL) OR
           (rule_id IS NOT NULL AND gpa_snapshot IS NOT NULL)),
    CHECK (grade_flag = 'NORMAL' OR
           (total_score IS NULL AND level_code IS NULL AND rule_id IS NULL))
) ENGINE=InnoDB;

CREATE TABLE grade_item (
    grade_id BIGINT UNSIGNED NOT NULL,
    component_id BIGINT UNSIGNED NOT NULL,
    item_score DECIMAL(5,2) NOT NULL CHECK (item_score BETWEEN 0 AND 100),
    PRIMARY KEY (grade_id, component_id),
    FOREIGN KEY (grade_id) REFERENCES grade (grade_id),
    FOREIGN KEY (component_id) REFERENCES grade_component (component_id)
) ENGINE=InnoDB;

CREATE TABLE grade_sheet_log (
    log_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    sheet_id BIGINT UNSIGNED NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    action_type ENUM('SUBMIT','RETURN','PUBLISH') NOT NULL,
    opinion VARCHAR(500),
    acted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_grade_sheet_log_time (sheet_id, acted_at, log_id),
    FOREIGN KEY (sheet_id) REFERENCES grade_sheet (sheet_id),
    FOREIGN KEY (actor_id) REFERENCES user_account (account_id)
) ENGINE=InnoDB;

CREATE TABLE request (
    request_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    applicant_id BIGINT UNSIGNED NOT NULL,
    request_type ENUM('INFO_CHANGE','RESCHEDULE','RETAKE','EXEMPT','DEFERRAL',
                      'GRADE_REVIEW','GRADE_CORRECTION') NOT NULL,
    state ENUM('PENDING','IN_REVIEW','APPROVED','REJECTED','WITHDRAWN') NOT NULL DEFAULT 'PENDING',
    reason VARCHAR(500) NOT NULL,
    requested_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_request_queue (state, request_type, requested_at, request_id),
    KEY ix_request_applicant (applicant_id, requested_at, request_id),
    FOREIGN KEY (applicant_id) REFERENCES user_account (account_id)
) ENGINE=InnoDB;

CREATE TABLE approval_log (
    approval_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    request_id BIGINT UNSIGNED NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    action_type ENUM('TAKE','APPROVE','REJECT','WITHDRAW') NOT NULL,
    opinion VARCHAR(500),
    acted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY ix_approval_request_time (request_id, acted_at, approval_id),
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (actor_id) REFERENCES user_account (account_id)
) ENGINE=InnoDB;

-- 信息变更父记录直接使用 request。字段白名单及加密在事务入口校验。
CREATE TABLE info_change_item (
    request_id BIGINT UNSIGNED NOT NULL,
    field_code ENUM('PHONE','EMAIL','ADDRESS','NAME_PINYIN') NOT NULL,
    old_value_cipher VARBINARY(2048),
    new_value_cipher VARBINARY(2048),
    PRIMARY KEY (request_id, field_code),
    FOREIGN KEY (request_id) REFERENCES request (request_id)
) ENGINE=InnoDB;

CREATE TABLE reschedule_req (
    request_id BIGINT UNSIGNED PRIMARY KEY,
    schedule_id BIGINT UNSIGNED NOT NULL,
    change_type ENUM('MOVE_ALL','MOVE_WEEKS','CANCEL_WEEKS','CHANGE_TEACHER') NOT NULL,
    original_schedule JSON NOT NULL,
    proposed_schedule JSON NOT NULL,
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (schedule_id) REFERENCES schedule (schedule_id)
) ENGINE=InnoDB;

CREATE TABLE retake_req (
    request_id BIGINT UNSIGNED PRIMARY KEY,
    course_code VARCHAR(20) NOT NULL,
    term_code CHAR(11) NOT NULL,
    replacement_course_code VARCHAR(20),
    study_type ENUM('RETAKE','MAKEUP_STUDY') NOT NULL,
    payment_state ENUM('NOT_REQUIRED','PENDING','PAID') NOT NULL DEFAULT 'NOT_REQUIRED',
    payment_ref VARCHAR(64),
    UNIQUE KEY uq_retake_payment_ref (payment_ref),
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (course_code) REFERENCES course (course_code),
    FOREIGN KEY (term_code) REFERENCES term (term_code),
    FOREIGN KEY (replacement_course_code) REFERENCES course (course_code),
    CHECK ((payment_state = 'PAID' AND payment_ref IS NOT NULL) OR
           (payment_state <> 'PAID' AND payment_ref IS NULL))
) ENGINE=InnoDB;

CREATE TABLE exempt_req (
    request_id BIGINT UNSIGNED PRIMARY KEY,
    enrollment_id BIGINT UNSIGNED NOT NULL,
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id)
) ENGINE=InnoDB;

CREATE TABLE deferral_req (
    request_id BIGINT UNSIGNED PRIMARY KEY,
    exam_seat_id BIGINT UNSIGNED NOT NULL,
    evidence_ref VARCHAR(250) NOT NULL,
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (exam_seat_id) REFERENCES exam_seat (exam_seat_id)
) ENGINE=InnoDB;

CREATE TABLE review_req (
    request_id BIGINT UNSIGNED PRIMARY KEY,
    grade_id BIGINT UNSIGNED NOT NULL,
    component_id BIGINT UNSIGNED,
    grade_version INT UNSIGNED NOT NULL CHECK (grade_version > 0),
    result_code ENUM('KEEP','CORRECTION_REQUESTED'),
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (grade_id) REFERENCES grade (grade_id),
    FOREIGN KEY (component_id) REFERENCES grade_component (component_id)
) ENGINE=InnoDB;

CREATE TABLE correction_req (
    request_id BIGINT UNSIGNED PRIMARY KEY,
    grade_id BIGINT UNSIGNED NOT NULL,
    review_request_id BIGINT UNSIGNED,
    expected_version INT UNSIGNED NOT NULL CHECK (expected_version > 0),
    old_snapshot JSON NOT NULL,
    proposed_snapshot JSON NOT NULL,
    FOREIGN KEY (request_id) REFERENCES request (request_id),
    FOREIGN KEY (grade_id) REFERENCES grade (grade_id),
    FOREIGN KEY (review_request_id) REFERENCES review_req (request_id)
) ENGINE=InnoDB;

CREATE TABLE grade_log (
    log_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    grade_id BIGINT UNSIGNED NOT NULL,
    correction_request_id BIGINT UNSIGNED NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    old_version INT UNSIGNED NOT NULL,
    new_version INT UNSIGNED NOT NULL,
    old_snapshot JSON NOT NULL,
    new_snapshot JSON NOT NULL,
    acted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_grade_log_correction (correction_request_id),
    UNIQUE KEY uq_grade_log_version (grade_id, new_version),
    FOREIGN KEY (grade_id) REFERENCES grade (grade_id),
    FOREIGN KEY (correction_request_id) REFERENCES correction_req (request_id),
    FOREIGN KEY (actor_id) REFERENCES user_account (account_id),
    CHECK (new_version = old_version + 1)
) ENGINE=InnoDB;

CREATE TABLE eval_scheme (
    scheme_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    scheme_code VARCHAR(24) NOT NULL,
    version_no SMALLINT UNSIGNED NOT NULL CHECK (version_no > 0),
    scheme_name VARCHAR(80) NOT NULL,
    UNIQUE KEY uq_eval_scheme_version (scheme_code, version_no)
) ENGINE=InnoDB;

CREATE TABLE eval_indicator (
    indicator_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    scheme_id BIGINT UNSIGNED NOT NULL,
    indicator_text VARCHAR(250) NOT NULL,
    weight_percent DECIMAL(5,2) NOT NULL CHECK (weight_percent > 0 AND weight_percent <= 100),
    max_score DECIMAL(6,2) NOT NULL CHECK (max_score > 0),
    order_no SMALLINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_eval_indicator_order (scheme_id, order_no),
    FOREIGN KEY (scheme_id) REFERENCES eval_scheme (scheme_id)
) ENGINE=InnoDB;

CREATE TABLE eval_batch (
    batch_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    term_code CHAR(11) NOT NULL,
    scheme_id BIGINT UNSIGNED NOT NULL,
    batch_name VARCHAR(80) NOT NULL,
    category VARCHAR(24) NOT NULL,
    opens_at DATETIME(3) NOT NULL,
    closes_at DATETIME(3) NOT NULL,
    state ENUM('DRAFT','OPEN','CLOSED','AGGREGATED') NOT NULL DEFAULT 'DRAFT',
    UNIQUE KEY uq_eval_batch_name (term_code, batch_name),
    FOREIGN KEY (term_code) REFERENCES term (term_code),
    FOREIGN KEY (scheme_id) REFERENCES eval_scheme (scheme_id),
    CHECK (closes_at > opens_at)
) ENGINE=InnoDB;

CREATE TABLE eval_target (
    target_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    batch_id BIGINT UNSIGNED NOT NULL,
    section_id BIGINT UNSIGNED NOT NULL,
    teacher_no VARCHAR(20) NOT NULL,
    UNIQUE KEY uq_eval_target (batch_id, section_id, teacher_no),
    KEY ix_eval_target_teacher (teacher_no, batch_id, target_id),
    FOREIGN KEY (batch_id) REFERENCES eval_batch (batch_id),
    FOREIGN KEY (section_id, teacher_no) REFERENCES section_teacher (section_id, teacher_no)
) ENGINE=InnoDB;

CREATE TABLE eval_assignment (
    assignment_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    target_id BIGINT UNSIGNED NOT NULL,
    enrollment_id BIGINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_eval_assignment (target_id, enrollment_id),
    FOREIGN KEY (target_id) REFERENCES eval_target (target_id),
    FOREIGN KEY (enrollment_id) REFERENCES enrollment (enrollment_id)
) ENGINE=InnoDB;

CREATE TABLE evaluation (
    assignment_id BIGINT UNSIGNED PRIMARY KEY,
    total_score DECIMAL(5,2) NOT NULL CHECK (total_score BETWEEN 0 AND 100),
    comment_text VARCHAR(1000),
    submitted_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (assignment_id) REFERENCES eval_assignment (assignment_id)
) ENGINE=InnoDB;

CREATE TABLE eval_answer (
    assignment_id BIGINT UNSIGNED NOT NULL,
    indicator_id BIGINT UNSIGNED NOT NULL,
    score DECIMAL(6,2) NOT NULL CHECK (score >= 0),
    PRIMARY KEY (assignment_id, indicator_id),
    FOREIGN KEY (assignment_id) REFERENCES evaluation (assignment_id),
    FOREIGN KEY (indicator_id) REFERENCES eval_indicator (indicator_id)
) ENGINE=InnoDB;

CREATE TABLE eval_result (
    target_id BIGINT UNSIGNED PRIMARY KEY,
    eligible_count INT UNSIGNED NOT NULL,
    response_count INT UNSIGNED NOT NULL,
    average_score DECIMAL(5,2) CHECK (average_score BETWEEN 0 AND 100),
    aggregated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (target_id) REFERENCES eval_target (target_id),
    CHECK (response_count <= eligible_count),
    CHECK ((response_count = 0 AND average_score IS NULL) OR
           (response_count > 0 AND average_score IS NOT NULL))
) ENGINE=InnoDB;

CREATE TABLE eval_indicator_result (
    target_id BIGINT UNSIGNED NOT NULL,
    indicator_id BIGINT UNSIGNED NOT NULL,
    average_score DECIMAL(6,2) NOT NULL CHECK (average_score >= 0),
    PRIMARY KEY (target_id, indicator_id),
    FOREIGN KEY (target_id) REFERENCES eval_result (target_id),
    FOREIGN KEY (indicator_id) REFERENCES eval_indicator (indicator_id)
) ENGINE=InnoDB;

-- 通用内部读模型。查询必须附加来自登录会话的数据范围条件。
CREATE VIEW v_student_grade AS
SELECT e.student_no, c.course_code, c.course_name,
       s.term_code AS course_term, w.term_code AS grade_term, w.attempt_kind,
       g.grade_id, g.total_score, g.level_code, g.grade_flag, g.gpa_snapshot,
       g.credits_snapshot, g.hours_snapshot, g.nature_snapshot,
       COALESCE(r.passed, 0) AS passed,
       COALESCE(g.total_score, r.comparable_score) AS comparison_score
FROM grade AS g
JOIN grade_sheet AS sh ON sh.sheet_id = g.sheet_id
JOIN grade_input_window AS w ON w.window_id = sh.window_id
JOIN enrollment AS e ON e.enrollment_id = g.enrollment_id
JOIN section AS s ON s.section_id = sh.section_id AND s.section_id = e.section_id
JOIN course AS c ON c.course_code = s.course_code
LEFT JOIN gpa_rule AS r ON r.rule_id = g.rule_id
WHERE sh.state = 'PUBLISHED';

CREATE VIEW v_teacher_roster AS
SELECT t.teacher_no, e.section_id, e.student_no, s.student_name,
       s.class_no, e.study_type
FROM section_teacher AS t
JOIN enrollment AS e ON e.section_id = t.section_id
JOIN student AS s ON s.student_no = e.student_no
WHERE e.state IN ('ENROLLED','COMPLETED');

CREATE VIEW v_teacher_eval_result AS
SELECT t.teacher_no, t.batch_id, t.section_id, t.target_id,
       r.eligible_count, r.response_count, r.average_score,
       i.indicator_id, i.average_score AS indicator_average
FROM eval_target AS t
JOIN eval_batch AS b ON b.batch_id = t.batch_id
JOIN eval_result AS r ON r.target_id = t.target_id
LEFT JOIN eval_indicator_result AS i ON i.target_id = r.target_id
WHERE b.state = 'AGGREGATED';
