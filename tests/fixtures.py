"""Deterministic, fictional records. All date/time inputs are UTC unless a time slot."""

from datetime import date, datetime

from database import script


TERM = "2026-2027-1"
NEXT_TERM = "2026-2027-2"
DIRECT_TIME = datetime(2026, 9, 5, 2)
LOTTERY_TIME = datetime(2026, 9, 12, 2)
AFTER_LOTTERY = datetime(2026, 9, 16, 2)
GRADE_TIME = datetime(2027, 1, 8, 2)
EVAL_TIME = datetime(2026, 12, 10, 2)
AFTER_EVAL = datetime(2026, 12, 22, 2)
ADMITTED_ON = date(2026, 9, 1)

ADMIN = 1
TEACHER, OTHER_TEACHER, THIRD_TEACHER = 2, 3, 4
ALICE, BOB, CHEN = 101, 102, 103

PERMISSIONS = {
    "admin": ("ADMISSION", "PROFILE_REVIEW", "SCHEDULE", "ROUND", "EXAM",
              "DEFERRAL_REVIEW", "GRADE_WINDOW", "GRADE_PUBLISH", "CORRECTION_REVIEW", "EVAL_ADMIN",
              "ACCOUNT_ADMIN", "ROLE_ADMIN", "CATALOG"),
    "student": ("SELF_PROFILE", "SELECT_COURSE", "EXAM_READ", "DEFERRAL", "GRADE_READ", "EVAL_SUBMIT"),
    "teacher": ("GRADE_WRITE", "CORRECTION_SUBMIT", "EVAL_READ", "ROSTER_READ", "EXAM_INVIGILATION"),
}


def student_no(account):
    return f"2026{account - 100:06d}"


def seed_reference(cn):
    script(cn, """
        INSERT INTO department VALUES ('CS','课程设计虚构学院');
        INSERT INTO major VALUES ('CS01','CS','软件工程',NULL,NULL,4);
        INSERT INTO admin_class VALUES
          ('2026010001','CS01',2026,'甲班'),
          ('2026010002','CS01',2026,'乙班'),
          ('2026010003','CS01',2026,'丙班');
        INSERT INTO term (term_code,starts_on,week_count,is_current) VALUES
          ('2026-2027-1','2026-08-31',20,1), ('2026-2027-2','2027-03-01',20,0);
        INSERT INTO campus VALUES ('N','北校区'),('S','南校区');
        INSERT INTO period_time VALUES
          ('N',1,'08:00','08:45'),('N',2,'08:55','09:40'),('N',3,'10:00','10:45'),
          ('N',4,'10:55','11:40'),('S',1,'07:00','07:45'),('S',2,'08:15','09:00'),
          ('S',3,'09:10','09:55'),('S',4,'10:00','10:45');
        INSERT INTO classroom VALUES
          ('R1','N','测试楼','一号教室',50,12,1,'FIXTURE','2026-08-31'),
          ('R2','N','测试楼','二号教室',50,12,1,'FIXTURE','2026-08-31'),
          ('R3','S','测试楼','三号教室',50,12,1,'FIXTURE','2026-08-31'),
          ('SMALL','N','测试楼','小教室',2,1,1,'FIXTURE','2026-08-31');
        INSERT INTO teacher VALUES
          ('T01','虚构教师甲','CS',NULL,1,'FIXTURE','2026-08-31'),
          ('T02','虚构教师乙','CS',NULL,1,'FIXTURE','2026-08-31'),
          ('T03','虚构教师丙','CS',NULL,1,'FIXTURE','2026-08-31');
        INSERT INTO user_account (account_id,login_name,password_hash,teacher_no) VALUES
          (1,'fixture-admin','fixture-disabled-password',NULL),
          (2,'fixture-teacher-a','fixture-disabled-password','T01'),
          (3,'fixture-teacher-b','fixture-disabled-password','T02'),
          (4,'fixture-teacher-c','fixture-disabled-password','T03');
        INSERT INTO role VALUES ('admin','管理员'),('teacher','教师'),('student','学生');
        INSERT INTO user_role VALUES (1,'admin'),(2,'teacher'),(3,'teacher'),(4,'teacher');
        INSERT INTO course (course_code,course_name,department_code,credits,lecture_hours,assessment,course_nature)
        VALUES ('DB','数据库','CS',3,48,'EXAM','专业课'),
               ('OS','操作系统','CS',3,48,'EXAM','专业课'),
               ('NET','计算机网络','CS',2,32,'EXAM','专业课'),
               ('MATH','离散数学','CS',2,32,'EXAM','专业课'),
               ('DIS','数据结构','CS',3,48,'EXAM','专业课'),
               ('PROJ','课程项目','CS',3,48,'ASSESSMENT','实践课');
        INSERT INTO program (program_id,major_code,entry_year,required_credits) VALUES (1,'CS01',2026,8);
        INSERT INTO program_course VALUES
          (1,'DB','专业核心','REQUIRED',1),
          (1,'OS','专业核心','REQUIRED',1),
          (1,'NET','专业基础','ELECTIVE',1);
        INSERT INTO grade_scale VALUES
          (1,'PERCENT',1,'百分制','NUMERIC','PUBLISHED'),
          (2,'LEVEL',1,'等级制','LEVEL','PUBLISHED');
        INSERT INTO gpa_rule
          (scale_id,min_score,max_score,level_code,comparable_score,gpa,passed) VALUES
          (1,0,59.99,NULL,NULL,0,0),(1,60,79.99,NULL,NULL,2,1),
          (1,80,89.99,NULL,NULL,3,1),(1,90,100,NULL,NULL,4,1),
          (2,NULL,NULL,'FAIL',50,0,0),(2,NULL,NULL,'PASS',70,2,1),
          (2,NULL,NULL,'GOOD',85,3,1),(2,NULL,NULL,'EXCELLENT',95,4,1);
    """)
    with cn.cursor() as cur:
        cur.executemany(
            "INSERT INTO permission VALUES (%s,%s)",
            [(code, code) for codes in PERMISSIONS.values() for code in codes],
        )
        cur.executemany(
            "INSERT INTO role_permission VALUES (%s,%s)",
            [(role, code) for role, codes in PERMISSIONS.items() for code in codes],
        )


def admission_rows():
    return [
        (student_no(account), f"虚构学生{account}",
         "2026010001" if account < 113 else "2026010002", ADMITTED_ON)
        for account in range(101, 121)
    ]


def student_accounts(cn):
    with cn.cursor() as cur:
        cur.executemany(
            """INSERT INTO user_account (account_id,login_name,password_hash,student_no)
               VALUES (%s,%s,'fixture-disabled-password',%s)""",
            [(account, f"fixture-s{account}", student_no(account)) for account in range(101, 121)],
        )
        cur.executemany(
            "INSERT INTO user_role VALUES (%s,'student')",
            [(account,) for account in range(101, 121)],
        )


def seed_sections(cn):
    """Set up the deterministic outputs of course planning and instructor assignment."""
    script(cn, """
        INSERT INTO section
          (section_id,offering_no,course_code,term_code,section_no,campus_code,
           offered_credits,offered_hours,offered_nature,offered_assessment,capacity)
        VALUES
          (1,'SEC-1','DB','2026-2027-1','01','N',3,48,'专业课','EXAM',2),
          (2,'SEC-2','OS','2026-2027-1','01','N',3,48,'专业课','EXAM',3),
          (3,'SEC-3','DB','2026-2027-1','02','N',3,48,'专业课','EXAM',2),
          (4,'SEC-4','NET','2026-2027-1','01','N',2,32,'专业课','EXAM',1),
          (5,'SEC-5','MATH','2026-2027-1','01','N',2,32,'专业课','EXAM',3),
          (6,'SEC-6','DIS','2026-2027-2','01','N',3,48,'专业课','EXAM',2),
          (7,'SEC-7','PROJ','2026-2027-1','01','N',3,48,'实践课','ASSESSMENT',20),
          (8,'SEC-8','MATH','2026-2027-1','02','S',2,32,'专业课','EXAM',3);
        INSERT INTO section_teacher VALUES
          (1,'T01','LEAD'),(2,'T02','LEAD'),(3,'T03','LEAD'),(4,'T02','LEAD'),
          (5,'T03','LEAD'),(6,'T01','LEAD'),(7,'T01','LEAD'),(8,'T03','LEAD');
        INSERT INTO section_class VALUES
          (1,'2026010001'),(2,'2026010002'),(3,'2026010003'),
          (4,'2026010002'),(5,'2026010001'),(7,'2026010003'),(8,'2026010003');
    """)
