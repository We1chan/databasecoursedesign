"""Integration tests for the five modules in the second-stage design."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
import json
from threading import Barrier
import unittest

from mysql.connector import Error

from accounts import add_student_account, authenticate, change_password, lock_account, set_role
from academic import (
    add_course, add_term, adjust_selection, apply_phone_change, change_selection, close_lottery,
    create_offering, import_students, open_round, replace_plan_courses, request_phone_change,
    schedule_section, student_profile, student_selections, student_timetable, teacher_roster,
)
from database import Rejected, connect, rows, sandbox, value
from evaluations import finish_evaluation, open_evaluation, submit_evaluation, teacher_results
from exams import approve_deferral, publish_exam, request_deferral, student_exams, teacher_invigilation
from fixtures import (
    ADMIN, ADMITTED_ON, AFTER_EVAL, AFTER_LOTTERY, ALICE, BOB, CHEN, DIRECT_TIME,
    EVAL_TIME, GRADE_TIME, LOTTERY_TIME, NEXT_TERM, OTHER_TEACHER, TEACHER, TERM,
    admission_rows, seed_reference, seed_sections, student_accounts, student_no,
)
from grades import (
    approve_correction, configure_grades, open_grade_sheet, program_credits, published_grades,
    request_correction, review_sheet, save_grades, submit_grades,
)


DIRECT_START = datetime(2026, 9, 3)
DIRECT_END = datetime(2026, 9, 10)
LOTTERY_START = datetime(2026, 9, 11)
LOTTERY_END = datetime(2026, 9, 15)
EXAM_START = datetime(2027, 1, 4, 1)
EXAM_END = datetime(2027, 1, 4, 3)
GRADE_OPEN = datetime(2027, 1, 5)
GRADE_CLOSE = datetime(2027, 1, 15)
EVAL_OPEN = datetime(2026, 12, 5)
EVAL_CLOSE = datetime(2026, 12, 20)


def numeric(enrollment_id, regular, final):
    return enrollment_id, {"平时": regular, "期末": final}, None, "NORMAL"


class Workflows(unittest.TestCase):
    def setUp(self):
        self.cn = self.enterContext(sandbox(optimized=True))
        seed_reference(self.cn)
        self.fixture = admission_rows()
        self.batch, self.is_new_batch = import_students(self.cn, ADMIN, "ADMISSION-2026", self.fixture)
        student_accounts(self.cn)
        seed_sections(self.cn)

    def data(self, sql, args=()):
        with self.cn.cursor(dictionary=True, buffered=True) as cur:
            return rows(cur, sql, args)

    def scalar(self, sql, args=()):
        with self.cn.cursor(dictionary=True, buffered=True) as cur:
            return value(cur, sql, args)

    def execute(self, sql, args=()):
        with self.cn.cursor() as cur:
            cur.execute(sql, args)

    def reject(self, code, operation, *args, **kwargs):
        with self.assertRaisesRegex(Rejected, f"^{code}$"):
            operation(self.cn, *args, **kwargs)

    def schedule(self, section, room, day, periods=(1, 1), weeks=frozenset({1, 2, 3})):
        return schedule_section(self.cn, ADMIN, section, room, day, periods, weeks)

    def standard_schedules(self, *, coincident=False):
        self.schedule(1, "R1", 1)
        self.schedule(2, "R2", 1 if coincident else 2)
        self.schedule(3, "R2", 4)
        self.schedule(4, "R2", 3, (2, 2))
        self.schedule(5, "R2", 5)
        self.schedule(8, "R3", 1, (2, 2))

    def direct_round(self, sections, **kwargs):
        return open_round(self.cn, ADMIN, TERM, "正选", "DIRECT", DIRECT_START,
                          DIRECT_END, sections, **kwargs)

    def choose(self, account, round_id, section, **kwargs):
        return change_selection(self.cn, account, round_id, section, DIRECT_TIME, **kwargs)[0]

    def section_count(self, section_id):
        counters = self.data(
            """SELECT s.enrolled_count,COUNT(e.enrollment_id) AS actual FROM section s
               LEFT JOIN enrollment e ON e.section_id=s.section_id AND e.state IN ('ENROLLED','COMPLETED')
               WHERE s.section_id=%s GROUP BY s.section_id""",
            (section_id,),
        )[0]
        self.assertEqual(counters["enrolled_count"], counters["actual"])
        return counters["actual"]

    def parallel_choices(self, requests, round_id):
        barrier = Barrier(len(requests), timeout=15)

        # Resolve the schema once before opening concurrent independent connections.
        database = self.cn.database

        def worker(entry):
            account, section = entry
            with connect(database) as cn:
                barrier.wait()
                try:
                    return change_selection(cn, account, round_id, section, DIRECT_TIME)[1]
                except Rejected as error:
                    return str(error)

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return list(pool.map(worker, requests))

    def test_s_admission_replay_and_atomic_failure(self):
        """S06 | 新生导入幂等、同批异内容冲突、外键错误整批回滚"""
        self.assertTrue(self.is_new_batch)
        self.assertEqual(import_students(self.cn, ADMIN, "ADMISSION-2026", list(reversed(self.fixture))),
                         (self.batch, False))
        self.reject("IMPORT_CONTENT_CHANGED", import_students, ADMIN, "ADMISSION-2026", self.fixture[:-1])
        self.reject("DUPLICATE_IMPORT_STUDENT", import_students, ADMIN, "duplicate", self.fixture[:1] * 2)
        with self.assertRaises(Error) as invalid:
            import_students(self.cn, ADMIN, "invalid", [
                ("2026000061", "虚构学生甲", "2026010001", ADMITTED_ON),
                ("2026000062", "虚构学生乙", "9999999999", ADMITTED_ON),
            ])
        self.assertEqual(invalid.exception.errno, 1452)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM student"), 20)
        self.assertEqual(self.scalar("SELECT state FROM import_batch WHERE source_batch_no='invalid'"), "FAILED")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM import_batch"), 2)
        self.assertEqual(
            [r["result_code"] for r in self.data(
                "SELECT result_code FROM operation_log WHERE action_code='IMPORT_STUDENTS' ORDER BY log_id")],
            ["OK", "SQL_1452"],
        )

    def test_s_profile_scope_staleness_and_history(self):
        """S01/S02/S05、A02 | 本人学籍、角色权限、过期原值拒绝和历史"""
        self.assertEqual(student_profile(self.cn, ALICE)["student_no"], student_no(ALICE))
        self.assertEqual(student_profile(self.cn, ALICE)["changes"], [])
        self.reject("FORBIDDEN", student_profile, OTHER_TEACHER)
        first = request_phone_change(self.cn, ALICE, b"fixture-sealed-first")
        stale = request_phone_change(self.cn, ALICE, b"fixture-sealed-stale")
        self.reject("FORBIDDEN", apply_phone_change, OTHER_TEACHER, first)
        self.assertIsNone(self.scalar("SELECT phone_cipher FROM student WHERE student_no=%s",
                                      (student_no(ALICE),)))
        self.assertTrue(apply_phone_change(self.cn, ADMIN, first))
        self.assertFalse(apply_phone_change(self.cn, ADMIN, first))
        self.reject("STALE_PROFILE", apply_phone_change, ADMIN, stale)
        self.assertEqual(student_profile(self.cn, ALICE)["phone_cipher"], b"fixture-sealed-first")
        self.assertIsNone(student_profile(self.cn, BOB)["phone_cipher"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM approval_log"), 1)

    def test_a_account_password_role_and_lock(self):
        """C01/A01/A02 | 密码哈希、身份校验、角色授予撤回和锁定"""
        import_students(self.cn, ADMIN, "ACCOUNT-TEST",
                        [("2026000050", "账户样例", "2026010001", ADMITTED_ON)])
        login, initial, updated = "fake-account", "fixture-pass-old", "fixture-pass-new"
        self.reject("FORBIDDEN", add_student_account, ALICE, login, "2026000050", initial)
        account = add_student_account(self.cn, ADMIN, login, "2026000050", initial)
        self.assertNotEqual(self.scalar("SELECT password_hash FROM user_account WHERE account_id=%s",
                                        (account,)), initial)
        self.assertEqual(authenticate(self.cn, login, initial, DIRECT_TIME), account)
        self.reject("FORBIDDEN", student_profile, account)
        self.reject("FORBIDDEN", set_role, account, account, "student", enabled=True)
        set_role(self.cn, ADMIN, account, "student", enabled=True)
        set_role(self.cn, ADMIN, account, "student", enabled=True)
        self.assertEqual(student_profile(self.cn, account)["student_no"], "2026000050")
        change_password(self.cn, account, initial, updated)
        self.reject("INVALID_CREDENTIALS", authenticate, login, initial, DIRECT_TIME)
        self.assertEqual(authenticate(self.cn, login, updated, DIRECT_TIME), account)
        set_role(self.cn, ADMIN, account, "student", enabled=False)
        self.reject("FORBIDDEN", student_profile, account)
        lock_account(self.cn, ADMIN, account)
        self.reject("INVALID_CREDENTIALS", authenticate, login, updated, DIRECT_TIME)
        self.assertEqual(self.scalar("SELECT last_login_at FROM user_account WHERE account_id=%s",
                                     (account,)), DIRECT_TIME)

    def test_k_catalog_and_course_plan_replacement(self):
        """K01–K03 | 小学期、课程主数据、培养方案及批量更新回滚"""
        small_term = "2026-2027-3"
        add_term(self.cn, ADMIN, small_term, datetime(2027, 7, 1).date(), 4, is_current=True)
        self.assertEqual(self.scalar("SELECT term_code FROM term WHERE is_current"), small_term)
        self.reject("FORBIDDEN", add_course, ALICE, "AI", "人工智能", "CS", 3, 48, "EXAM", "专业课")
        add_course(self.cn, ADMIN, "AI", "人工智能", "CS", 3, 48, "EXAM", "专业课")
        replace_plan_courses(self.cn, ADMIN, 1, [("AI", "专业基础", "ELECTIVE", 3)])
        with self.assertRaises(Error) as error:
            replace_plan_courses(self.cn, ADMIN, 1, [("UNKNOWN", "专业基础", "REQUIRED", 1)])
        self.assertEqual(error.exception.errno, 1452)
        self.assertEqual(self.data("SELECT course_code,course_attribute,suggested_term FROM program_course"),
                         [{"course_code": "AI", "course_attribute": "ELECTIVE", "suggested_term": 3}])

    def test_k_create_offering_from_program_and_teacher_roster(self):
        """K03/K06/K07/K18 | 培养方案开班、主讲安排、课表和教师名单"""
        self.reject("COURSE_NOT_IN_PLANNED_TERM", create_offering, ADMIN, TERM, "2026010001",
                    2, "DB", "03", "FIXTURE-9", "N", 4, "T01")
        section = create_offering(self.cn, ADMIN, TERM, "2026010001", 1, "DB", "03",
                                  "FIXTURE-9", "N", 4, "T01")
        self.assertEqual(self.scalar("SELECT class_no FROM section_class WHERE section_id=%s", (section,)),
                         "2026010001")
        self.execute("UPDATE course SET credits=4 WHERE course_code='DB'")
        self.assertEqual(self.scalar("SELECT offered_credits FROM section WHERE section_id=%s", (section,)), 3)
        self.schedule(section, "R1", 1)
        round_id = self.direct_round([section], credit_limit=3)
        self.choose(ALICE, round_id, section)
        roster = teacher_roster(self.cn, TEACHER, section)
        self.assertEqual([r["student_no"] for r in roster], [student_no(ALICE)])
        self.assertEqual(set(roster[0]), {"student_no", "student_name", "class_no", "study_type"})
        self.assertEqual(teacher_roster(self.cn, OTHER_TEACHER, section), [])

    def test_k_scheduling_and_conflicts(self):
        """K06–K08a | 教室、教师、行政班、容量及周次边界"""
        first = self.schedule(1, "R1", 1, weeks={1, 3})
        self.reject("ROOM_CONFLICT", schedule_section, ADMIN, 2, "R1", 1, (1, 1), {3})
        self.reject("TEACHER_CONFLICT", schedule_section, ADMIN, 7, "R2", 1, (1, 1), {3})
        self.reject("CLASS_CONFLICT", schedule_section, ADMIN, 5, "R2", 1, (1, 1), {3})
        self.reject("CLASSROOM_CAPACITY", schedule_section, ADMIN, 7, "SMALL", 2, (2, 2), {1})
        self.reject("INVALID_WEEK", schedule_section, ADMIN, 2, "R2", 2, (1, 1), {21})
        second = self.schedule(2, "R1", 1, weeks={2})
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM schedule"), 2)
        self.assertEqual(self.data("SELECT schedule_id,week_no FROM schedule_week ORDER BY schedule_id,week_no"),
                         [{"schedule_id": first, "week_no": 1},
                          {"schedule_id": first, "week_no": 3},
                          {"schedule_id": second, "week_no": 2}])

    def test_k_direct_rules_drop_reselect_and_timetable(self):
        """K09/K10/K12/K16 | 轮次准入、跨校区冲突、学分上限、退选与课表"""
        self.standard_schedules()
        self.reject("ROUND_WRONG_TERM", open_round, ADMIN, TERM, "错误学期", "DIRECT",
                    DIRECT_START, DIRECT_END, [6])
        round_id = self.direct_round([1, 2, 3, 4, 8], credit_limit=5, classes=["2026010001"])
        self.reject("ROUND_OVERLAP", open_round, ADMIN, TERM, "重叠时段", "DIRECT",
                    DIRECT_START, DIRECT_END, [1])
        self.reject("ROUND_CLOSED", change_selection, ALICE, round_id, 1, DIRECT_END)
        self.reject("CLASS_OUTSIDE_ROUND", change_selection, 115, round_id, 1, DIRECT_TIME)
        self.reject("SECTION_NOT_OPEN", change_selection, ALICE, round_id, 5, DIRECT_TIME)
        self.execute("UPDATE student SET academic_state='SUSPENDED' WHERE student_no=%s",
                     (student_no(105),))
        self.reject("STUDENT_NOT_ACTIVE", change_selection, 105, round_id, 1, DIRECT_TIME)
        selected = self.choose(ALICE, round_id, 1)
        self.assertEqual(self.choose(ALICE, round_id, 1), selected)
        self.reject("DUPLICATE_COURSE", change_selection, ALICE, round_id, 3, DIRECT_TIME)
        self.reject("CREDIT_LIMIT", change_selection, ALICE, round_id, 2, DIRECT_TIME)
        self.reject("TIMETABLE_CONFLICT", change_selection, ALICE, round_id, 8, DIRECT_TIME)
        self.assertEqual([r["section_id"] for r in student_timetable(self.cn, ALICE, TERM, 1)], [1])
        self.assertEqual(student_timetable(self.cn, BOB, TERM, 1), [])
        choices = student_selections(self.cn, ALICE, TERM)
        self.assertEqual([(r["course_code"], r["instructors"], r["offered_credits"]) for r in choices],
                         [("DB", "虚构教师甲", 3)])
        self.assertEqual(student_selections(self.cn, BOB, TERM), [])
        self.assertEqual(self.choose(ALICE, round_id, 1, drop=True), selected)
        self.assertEqual(self.choose(ALICE, round_id, 1, drop=True), selected)
        self.assertEqual(self.section_count(1), 0)
        self.assertEqual(student_timetable(self.cn, ALICE, TERM, 1), [])
        self.assertEqual(student_selections(self.cn, ALICE, TERM), [])
        self.assertEqual(self.choose(ALICE, round_id, 1), selected)
        self.assertEqual(self.section_count(1), 1)
        self.assertEqual([r["action_type"] for r in self.data("SELECT action_type FROM enroll_log ORDER BY log_id")],
                         ["SELECT", "DROP", "SELECT"])

    def test_k_concurrent_last_seat(self):
        """K10a | 12 个独立连接争抢 1 个名额，失败请求无明细"""
        self.schedule(4, "R2", 3)
        round_id = self.direct_round([4])
        outcomes = self.parallel_choices([(account, 4) for account in range(101, 113)], round_id)
        self.assertCountEqual(outcomes, ["ENROLLED"] + ["FULL"] * 11)
        self.assertEqual(self.section_count(4), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM enroll_log"), 1)

    def test_k_concurrent_same_student(self):
        """K10a | 同一学生两个冲突教学班的并发提交"""
        self.standard_schedules(coincident=True)
        round_id = self.direct_round([1, 2])
        outcomes = self.parallel_choices([(ALICE, 1), (ALICE, 2)], round_id)
        self.assertCountEqual(outcomes, ["ENROLLED", "TIMETABLE_CONFLICT"])
        self.assertEqual(self.section_count(1) + self.section_count(2), 1)

    def test_k_lottery_pending_drop_order_and_replay(self):
        """K10/K11 | 预选不占名额、退选、固定顺序、稳定封轮重试"""
        self.schedule(4, "R2", 3)
        round_id = open_round(self.cn, ADMIN, TERM, "预选", "LOTTERY", LOTTERY_START, LOTTERY_END, [4])
        pending = [change_selection(self.cn, actor, round_id, 4, LOTTERY_TIME)[0]
                   for actor in (ALICE, BOB, CHEN)]
        change_selection(self.cn, CHEN, round_id, 4, LOTTERY_TIME, drop=True)
        self.assertEqual(self.section_count(4), 0)
        seed = b"B" * 32
        self.reject("LOTTERY_STILL_OPEN", close_lottery, ADMIN, round_id, LOTTERY_TIME, seed)
        expected = self.scalar("""SELECT enrollment_id FROM enrollment WHERE state='PENDING'
                                  ORDER BY SHA2(CONCAT(HEX(%s),':',enrollment_id),256),enrollment_id LIMIT 1""",
                                (seed,))
        resolved = close_lottery(self.cn, ADMIN, round_id, AFTER_LOTTERY, seed)
        self.assertEqual(resolved[0], (expected, "ENROLLED"))
        self.assertCountEqual([state for _, state in resolved], ["ENROLLED", "REJECTED"])
        self.assertEqual(self.scalar("SELECT state FROM enrollment WHERE enrollment_id=%s", (pending[-1],)),
                         "DROPPED")
        self.assertEqual(close_lottery(self.cn, ADMIN, round_id, AFTER_LOTTERY, seed), [])
        self.assertEqual(self.section_count(4), 1)
        self.assertEqual(self.scalar("SELECT reason FROM enroll_log WHERE action_type='LOSE'"), "FULL")

    def test_k_admin_adjustment_uses_capacity_and_audit(self):
        """K11 | 管理员手工补退沿用容量校验且审计可查"""
        self.schedule(4, "R2", 3)
        round_id = self.direct_round([4])
        self.choose(ALICE, round_id, 4)
        self.reject("FORBIDDEN", adjust_selection, OTHER_TEACHER, round_id, student_no(ALICE), 4,
                    DIRECT_END, drop=True, reason="教务调整")
        self.reject("FULL", adjust_selection, ADMIN, round_id, student_no(BOB), 4,
                    DIRECT_END, drop=False, reason="重新核对后补选")
        adjust_selection(self.cn, ADMIN, round_id, student_no(ALICE), 4,
                         DIRECT_END, drop=True, reason="教务退选")
        adjust_selection(self.cn, ADMIN, round_id, student_no(BOB), 4,
                         DIRECT_END, drop=False, reason="教务补选")
        self.assertEqual(self.section_count(4), 1)
        self.assertEqual(
            [(r["actor_id"], r["new_state"], r["reason"]) for r in self.data(
                "SELECT actor_id,new_state,reason FROM enroll_log WHERE action_type='ADJUST' ORDER BY log_id")],
            [(ADMIN, "DROPPED", "教务退选"), (ADMIN, "ENROLLED", "教务补选")],
        )

    def test_e_exam_publication_conflicts_and_deferral(self):
        """E01–E06 | 多考场、占用/考生/监考冲突、本人查询和跨学期缓考"""
        self.standard_schedules()
        round_id = self.direct_round([1, 2, 4])
        alice_enrollment = self.choose(ALICE, round_id, 1)
        bob_enrollment = self.choose(BOB, round_id, 1)
        self.choose(ALICE, round_id, 2)
        self.choose(CHEN, round_id, 4)
        self.reject("EXAM_CAPACITY", publish_exam, ADMIN, 1, TERM, "FINAL", "REGULAR",
                    EXAM_START, EXAM_END, ["SMALL"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM exam"), 0)
        first_exam = publish_exam(self.cn, ADMIN, 1, TERM, "FINAL", "REGULAR",
                                  EXAM_START, EXAM_END, ["SMALL", "R1"],
                                  invigilators={"SMALL": "T01", "R1": "T03"})
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM exam_room WHERE exam_id=%s", (first_exam,)), 2)
        self.reject("EXAM_ROOM_CONFLICT", publish_exam, ADMIN, 2, TERM, "FINAL", "REGULAR",
                    EXAM_START, EXAM_END, ["R1"])
        self.reject("EXAM_STUDENT_CONFLICT", publish_exam, ADMIN, 2, TERM, "FINAL", "REGULAR",
                    EXAM_START, EXAM_END, ["R2"])
        self.reject("INVIGILATOR_CONFLICT", publish_exam, ADMIN, 4, TERM, "FINAL", "REGULAR",
                    EXAM_START, EXAM_END, ["R2"], invigilators={"R2": "T01"})
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM exam"), 1)
        alice_seats = student_exams(self.cn, ALICE, TERM)
        self.assertEqual([(r["exam_id"], r["room_code"], r["seat_no"]) for r in alice_seats],
                         [(first_exam, "SMALL", 1)])
        self.assertEqual(student_exams(self.cn, CHEN, TERM), [])
        self.assertEqual([r["room_code"] for r in teacher_invigilation(self.cn, TEACHER, TERM)],
                         ["SMALL"])
        self.assertEqual(teacher_invigilation(self.cn, OTHER_TEACHER, TERM), [])
        self.reject("FORBIDDEN", request_deferral, BOB, alice_seats[0]["exam_seat_id"], "sealed-ref",
                    datetime(2027, 1, 1))
        request = request_deferral(self.cn, ALICE, alice_seats[0]["exam_seat_id"], "sealed-ref",
                                   datetime(2027, 1, 1))
        self.assertTrue(approve_deferral(self.cn, ADMIN, request, datetime(2027, 1, 2)))
        self.assertFalse(approve_deferral(self.cn, ADMIN, request, datetime(2027, 1, 2)))
        self.assertEqual(student_exams(self.cn, ALICE, TERM)[0]["candidate_state"], "DEFERRED")
        followup = publish_exam(self.cn, ADMIN, 1, NEXT_TERM, "BEGINNING", "DEFERRED",
                                datetime(2027, 3, 2, 1), datetime(2027, 3, 2, 3), ["R1"])
        self.assertEqual([r["exam_id"] for r in student_exams(self.cn, ALICE, NEXT_TERM)], [followup])
        self.assertEqual(student_exams(self.cn, BOB, NEXT_TERM), [])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM approval_log"), 1)
        sheet = open_grade_sheet(self.cn, ADMIN, 1, TERM, "REGULAR", GRADE_OPEN, GRADE_CLOSE, first_exam)
        configure_grades(self.cn, TEACHER, sheet, 1, {"期末": 100}, GRADE_TIME)
        self.reject("GRADE_DEFERRAL_MISMATCH", save_grades, TEACHER, sheet,
                    [(alice_enrollment, {"期末": 80}, None, "NORMAL")], GRADE_TIME)
        save_grades(self.cn, TEACHER, sheet,
                    [(alice_enrollment, {}, None, "DEFERRED"),
                     (bob_enrollment, {"期末": 85}, None, "NORMAL")], GRADE_TIME)
        submit_grades(self.cn, TEACHER, sheet, GRADE_TIME)
        review_sheet(self.cn, ADMIN, sheet, True, GRADE_TIME)
        deferred = published_grades(self.cn, ALICE)[0]
        self.assertEqual(deferred["grade_flag"], "DEFERRED")
        self.assertIsNone(deferred["total_score"])

    def test_g_grades_visibility_correction_makeup_and_credits(self):
        """G01–G06/G09–G10、K04/K05 | 批量回滚、发布可见、改分、补考与培养学分"""
        self.schedule(1, "R1", 1)
        round_id = self.direct_round([1])
        alice_enrollment = self.choose(ALICE, round_id, 1)
        bob_enrollment = self.choose(BOB, round_id, 1)
        exam = publish_exam(self.cn, ADMIN, 1, TERM, "FINAL", "REGULAR",
                            EXAM_START, EXAM_END, ["R1"])
        sheet = open_grade_sheet(self.cn, ADMIN, 1, TERM, "REGULAR", GRADE_OPEN, GRADE_CLOSE, exam)
        self.reject("FORBIDDEN", configure_grades, OTHER_TEACHER, sheet, 1, {"期末": 100}, GRADE_TIME)
        self.reject("GRADE_WINDOW_CLOSED", configure_grades, TEACHER, sheet, 1, {"期末": 100}, GRADE_CLOSE)
        self.reject("GRADE_WEIGHTS", configure_grades, TEACHER, sheet, 1, {"平时": 20, "期末": 70}, GRADE_TIME)
        configure_grades(self.cn, TEACHER, sheet, 1, {"平时": 30, "期末": 70}, GRADE_TIME)
        self.reject("GRADE_SCORE_RANGE", save_grades, TEACHER, sheet,
                    [numeric(alice_enrollment, 80, 90), numeric(bob_enrollment, 20, 101)], GRADE_TIME)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM grade"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM grade_item"), 0)
        alice_grade = save_grades(self.cn, TEACHER, sheet, [numeric(alice_enrollment, 80, 90)],
                                  GRADE_TIME)[alice_enrollment]
        self.reject("GRADE_INCOMPLETE", submit_grades, TEACHER, sheet, GRADE_TIME)
        save_grades(self.cn, TEACHER, sheet, [numeric(bob_enrollment, 50, 50)], GRADE_TIME)
        submit_grades(self.cn, TEACHER, sheet, GRADE_TIME)
        self.assertEqual(published_grades(self.cn, ALICE), [])
        self.reject("GRADE_SHEET_LOCKED", save_grades, TEACHER, sheet,
                    [numeric(alice_enrollment, 99, 99)], GRADE_TIME)
        review_sheet(self.cn, ADMIN, sheet, False, GRADE_TIME, "核对后重新提交")
        submit_grades(self.cn, TEACHER, sheet, GRADE_TIME)
        review_sheet(self.cn, ADMIN, sheet, True, GRADE_TIME)
        self.assertEqual(published_grades(self.cn, ALICE)[0]["total_score"], Decimal("87.00"))
        self.assertEqual(published_grades(self.cn, BOB)[0]["total_score"], Decimal("50.00"))
        self.assertEqual(published_grades(self.cn, CHEN), [])
        self.assertEqual(program_credits(self.cn, ALICE), (3, 8))
        self.assertEqual(program_credits(self.cn, BOB), (0, 8))
        first = request_correction(self.cn, TEACHER, alice_grade, {"平时": 90, "期末": 100}, "复核分项")
        stale = request_correction(self.cn, TEACHER, alice_grade, {"平时": 99, "期末": 100}, "同期申请")
        self.assertTrue(approve_correction(self.cn, ADMIN, first))
        self.assertFalse(approve_correction(self.cn, ADMIN, first))
        self.reject("STALE_GRADE_VERSION", approve_correction, ADMIN, stale)
        self.assertEqual(published_grades(self.cn, ALICE)[0]["total_score"], Decimal("97.00"))
        log = self.data("SELECT old_snapshot,new_snapshot FROM grade_log")[0]
        self.assertEqual(json.loads(log["old_snapshot"])["total"], "87.00")
        self.assertEqual(json.loads(log["new_snapshot"])["gpa"], "4.00")
        self.assertEqual([r["action_type"] for r in self.data(
            "SELECT action_type FROM grade_sheet_log ORDER BY log_id")], ["SUBMIT", "RETURN", "SUBMIT", "PUBLISH"])

        makeup = publish_exam(self.cn, ADMIN, 1, NEXT_TERM, "BEGINNING", "MAKEUP",
                              datetime(2027, 3, 3, 1), datetime(2027, 3, 3, 3), ["R1"])
        self.assertEqual(student_exams(self.cn, ALICE, NEXT_TERM), [])
        self.assertEqual([r["exam_id"] for r in student_exams(self.cn, BOB, NEXT_TERM)], [makeup])
        makeup_at = datetime(2027, 3, 5, 1)
        makeup_sheet = open_grade_sheet(self.cn, ADMIN, 1, NEXT_TERM, "MAKEUP",
                                        datetime(2027, 3, 4), datetime(2027, 3, 10), makeup)
        configure_grades(self.cn, TEACHER, makeup_sheet, 2, {}, makeup_at)
        self.reject("GRADE_STUDENT_NOT_IN_ROSTER", save_grades, TEACHER, makeup_sheet,
                    [(alice_enrollment, {}, "GOOD", "NORMAL")], makeup_at)
        save_grades(self.cn, TEACHER, makeup_sheet, [(bob_enrollment, {}, "GOOD", "NORMAL")], makeup_at)
        submit_grades(self.cn, TEACHER, makeup_sheet, makeup_at)
        review_sheet(self.cn, ADMIN, makeup_sheet, True, makeup_at)
        self.assertEqual(len(published_grades(self.cn, BOB)), 2)
        self.assertEqual([r["level_code"] for r in published_grades(self.cn, BOB, best=True)], ["GOOD"])
        self.assertEqual(program_credits(self.cn, BOB), (3, 8))

    def test_p_evaluation_scope_boundaries_and_frozen_aggregate(self):
        """P01–P05 | 待评资格、防重复、时间窗、得分与教师汇总"""
        self.schedule(1, "R1", 1)
        self.schedule(4, "R2", 3)
        round_id = self.direct_round([1, 4])
        alice_enrollment = self.choose(ALICE, round_id, 1)
        self.choose(BOB, round_id, 1)
        self.choose(CHEN, round_id, 4)
        self.reject("EVAL_WEIGHTS", open_evaluation, ADMIN, TERM, EVAL_OPEN, EVAL_CLOSE,
                    [("课堂组织", 80, 10)])
        batch = open_evaluation(self.cn, ADMIN, TERM, EVAL_OPEN, EVAL_CLOSE,
                                [("课堂组织", 60, 10), ("反馈", 40, 20)])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM eval_assignment"), 3)
        assignment = self.scalar("SELECT assignment_id FROM eval_assignment WHERE enrollment_id=%s",
                                 (alice_enrollment,))
        indicators = [r["indicator_id"] for r in self.data("SELECT indicator_id FROM eval_indicator ORDER BY order_no")]
        scores = dict(zip(indicators, [8, 15]))
        self.reject("FORBIDDEN", submit_evaluation, BOB, batch, assignment, scores, EVAL_TIME)
        self.reject("EVAL_WRONG_INDICATORS", submit_evaluation, ALICE, batch, assignment,
                    {indicators[0]: 8}, EVAL_TIME)
        self.reject("EVAL_SCORE_RANGE", submit_evaluation, ALICE, batch, assignment,
                    dict(zip(indicators, [11, 15])), EVAL_TIME)
        self.reject("EVAL_CLOSED", submit_evaluation, ALICE, batch, assignment, scores, EVAL_CLOSE)
        self.assertEqual(submit_evaluation(self.cn, ALICE, batch, assignment, scores, EVAL_TIME,
                                           "虚构评语"), Decimal("78.00"))
        self.reject("EVAL_ALREADY_SUBMITTED", submit_evaluation, ALICE, batch, assignment, scores, EVAL_TIME)
        self.assertEqual(teacher_results(self.cn, TEACHER, batch, 1), [])
        self.reject("EVAL_STILL_OPEN", finish_evaluation, ADMIN, batch, EVAL_TIME)
        self.assertTrue(finish_evaluation(self.cn, ADMIN, batch, AFTER_EVAL))
        self.assertFalse(finish_evaluation(self.cn, ADMIN, batch, AFTER_EVAL))
        self.assertEqual(teacher_results(self.cn, TEACHER, batch, 2), [])
        self.assertEqual(teacher_results(self.cn, OTHER_TEACHER, batch, 1), [])
        results = teacher_results(self.cn, TEACHER, batch, 1)
        self.assertEqual([r["indicator_average"] for r in results], [8, 15])
        self.assertTrue(all((r["eligible_count"], r["response_count"], r["average_score"]) == (2, 1, 78)
                            for r in results))
        self.assertFalse({"student_no", "comment_text", "assignment_id"} & results[0].keys())

    def test_db_constraints_and_backend_prerequisites(self):
        """DDL | CHECK、外键和唯一键拒绝非法写入"""
        self.schedule(4, "R2", 3)
        round_id = self.direct_round([4])
        self.choose(ALICE, round_id, 4)
        for sql, errno in [
            ("UPDATE section SET enrolled_count=2 WHERE section_id=4", 3819),
            ("INSERT INTO round_section VALUES (1,999)", 1452),
            ("INSERT INTO enrollment (section_id,student_no,round_id,state)"
             " VALUES (4,'2026000001',1,'ENROLLED')", 1062),
        ]:
            with self.subTest(error=errno), self.assertRaises(Error) as error:
                self.execute(sql)
            self.assertEqual(error.exception.errno, errno)
        self.assertEqual(self.section_count(4), 1)
        self.assertEqual(self.scalar("""SELECT COUNT(*) FROM information_schema.tables
                                       WHERE table_schema=DATABASE() AND ENGINE='InnoDB'"""), 63)
        self.assertEqual(self.scalar("""SELECT COUNT(*) FROM information_schema.statistics
                                       WHERE table_schema=DATABASE() AND table_name='request'
                                       AND index_name='ix_request_queue_time'"""), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
