"""Exercise the public API and frontend session protocol against a disposable MySQL schema."""

import unittest
from datetime import datetime
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from database import connect, sandbox
from backend import accounts
from backend.bootstrap import install_schema, provision_admin
from backend.errors import Fault
from backend.main import create_app
from backend.settings import Settings


TERM = "2026-2027-1"
BASE = "/api/v1"
ORIGIN = "https://testserver:5173"
ADMIN_PASSWORD = "test-admin-password"
STUDENT_PASSWORD = "test-student-password"
TEACHER_PASSWORD = "test-teacher-password"


class Api(unittest.TestCase):
    def setUp(self):
        current = self.enterContext(sandbox())
        self.database = current.database
        install_schema(current)
        provision_admin(current, "admin", ADMIN_PASSWORD)
        self.now = datetime(2026, 11, 23, 1)
        self.app = create_app(
            Settings(data_key=Fernet.generate_key().decode("ascii"), frontend_origins=(ORIGIN,)),
            connect=lambda: connect(self.database), clock=lambda: self.now,
        )
        self.admin = self.client()
        self.login(self.admin, "admin", ADMIN_PASSWORD)

    def client(self):
        return self.enterContext(TestClient(self.app, base_url="https://testserver"))

    def call(self, client, method, path, data=None, *, status=200, csrf=True, origin=ORIGIN):
        headers = {"Origin": origin}
        if csrf and hasattr(client, "csrf"):
            headers["X-CSRF-Token"] = client.csrf
        response = client.request(method, BASE + path, json=data, headers=headers)
        self.assertEqual(response.status_code, status, f"{method} {path}: {response.text}")
        return response.json()["data" if status < 400 else "error"]

    def login(self, client, login_name, password):
        session = self.call(client, "POST", "/auth/login", {"login_name":login_name, "password":password})
        client.csrf = session["csrf_token"]
        return session

    def add_test_catalog(self):
        data = {
            "departments":[{"department_code":"CS","department_name":"计算机学院"}],
            "majors":[{"major_code":"CS01","major_name":"软件工程","department_code":"CS","study_years":4}],
            "classes":[{"class_no":"2026010001","major_code":"CS01","class_name":"一班","entry_year":2026}],
            "terms":[{"term_code":TERM,"starts_on":"2026-11-23","week_count":16,"is_current":True}],
            "campuses":[{"campus_code":"N","campus_name":"校区"}],
            "period_times":[{"campus_code":"N","period_no":1,"starts_at":"08:00:00","ends_at":"08:45:00"}],
            "teachers":[
                {"teacher_no":"T01","teacher_name":"样例教师","department_code":"CS"},
                {"teacher_no":"T02","teacher_name":"其它教师","department_code":"CS"},
            ],
            "rooms":[{"room_code":"R1","campus_code":"N","building":"教学楼","room_name":"101",
                      "seat_count":20,"exam_capacity":10}],
            "courses":[{"course_code":"DB","course_name":"数据库","department_code":"CS","credits":"3.00",
                        "lecture_hours":48,"assessment":"EXAM","course_nature":"专业课"}],
        }
        self.assertTrue(self.call(self.admin, "POST", "/admin/catalog", data)["applied"])
        self.assertEqual(self.call(self.admin, "GET", "/admin/catalog/period_times")["items"][0]["starts_at"], "08:00:00")
        self.assertEqual(self.call(self.admin, "GET", "/catalog/courses?search=数据库")["items"][0]["course_code"], "DB")
        self.assertEqual(self.call(self.admin, "GET", "/catalog/terms")[0]["term_code"], TERM)
        return data

    def add_students(self):
        data = {"batch_no":"COURSE-API-1", "students":[
            {"student_no":no, "student_name":name, "class_no":"2026010001","admitted_on":"2026-08-31"}
            for no, name in (("2026000001","甲"), ("2026000002","乙"))
        ]}
        imported = self.call(self.admin, "POST", "/admin/student-imports", data)
        self.assertTrue(imported["created"])
        self.assertEqual(self.call(self.admin,"POST","/admin/student-imports",data),
                         {"batch_id":imported["batch_id"],"created":False})
        self.assertEqual(len(self.call(self.admin,"GET","/admin/students?class_no=2026010001")["items"]),2)
        actors = []
        for login, no in (("alice","2026000001"),("bob","2026000002")):
            account = self.call(self.admin, "POST", "/admin/accounts",
                                {"login_name":login,"password":STUDENT_PASSWORD,"role":"student",
                                 "student_no":no}, status=201)
            client=self.client()
            self.assertEqual(self.login(client, login, STUDENT_PASSWORD)["account_id"], account["account_id"])
            actors.append(client)
        return actors

    def add_teacher(self, login, teacher_no):
        self.call(self.admin, "POST", "/admin/accounts",
                  {"login_name":login,"password":TEACHER_PASSWORD,"role":"teacher","teacher_no":teacher_no},
                  status=201)
        teacher = self.client()
        self.login(teacher, login, TEACHER_PASSWORD)
        return teacher

    def test_login_preserves_password_spaces_and_isolates_shared_ips(self):
        initial = "  test-spaced-password  "
        changed = "  test-changed-password  "
        with connect(self.database) as cn:
            provision_admin(cn, "spaced", initial)
        client = self.client()
        self.login(client, " spaced ", initial)
        self.call(client, "PATCH", "/auth/password", {"old_password": initial, "new_password": changed})
        self.login(client, "spaced", changed)
        self.call(client, "POST", "/auth/login", {"login_name": "spaced", "password": changed.strip()},
                  status=401)

        with patch.object(accounts, "matches", wraps=accounts.matches) as verify:
            error = self.call(client, "POST", "/auth/login",
                              {"login_name": "unregistered", "password": changed}, status=401)
            self.assertEqual(error["code"], "INVALID_CREDENTIALS")
            verify.assert_called_once_with(changed, accounts.DUMMY_PASSWORD_RECORD)

        gate = self.app.state.login_gate
        for _ in range(20):
            gate.observe("shared-ip", "SampleUser")
        gate.observe("shared-ip", "another-user")
        with self.assertRaisesRegex(Fault, "LOGIN_RATE_LIMITED"):
            gate.observe("shared-ip", "sampleuser")

    def test_catalog_protects_existing_meetings_and_keeps_current_term(self):
        data = self.add_test_catalog()
        self.call(self.admin, "POST", "/admin/catalog", {
            "campuses": [{"campus_code": "S", "campus_name": "另一个校区"}],
        })
        self.call(self.admin, "PUT", "/admin/programs", {
            "major_code": "CS01", "entry_year": 2026, "required_credits": "3.00",
            "courses": [{"course_code": "DB", "curriculum_group": "核心",
                         "course_attribute": "REQUIRED", "suggested_term": 1}],
        })
        section = self.call(self.admin, "POST", "/admin/sections", {
            "term_code": TERM, "class_no": "2026010001", "planned_term": 1, "course_code": "DB",
            "section_no": "01", "offering_no": "DB-01", "campus_code": "N", "capacity": 15,
            "teacher_no": "T01",
        }, status=201)["section_id"]
        self.call(self.admin, "POST", f"/admin/sections/{section}/meetings", {
            "room_code": "R1", "day_of_week": 1, "first_period": 1, "last_period": 1, "weeks": [1, 4],
        }, status=201)

        term = {"term_code": TERM, "starts_on": "2026-11-23", "week_count": 4}
        room = data["rooms"][0]
        for update, code in (
            ({"rooms": [{**room, "campus_code": "S"}]}, "ROOM_IN_USE"),
            ({"rooms": [{**room, "seat_count": 14}]}, "ROOM_IN_USE"),
            ({"rooms": [{**room, "is_active": False}]}, "ROOM_IN_USE"),
            ({"terms": [{**term, "week_count": 3}]}, "TERM_IN_USE"),
            ({"terms": [{**term, "starts_on": "2026-11-24"}]}, "TERM_IN_USE"),
        ):
            with self.subTest(update=update):
                error = self.call(self.admin, "POST", "/admin/catalog", update, status=409)
                self.assertEqual(error["code"], code)
        self.call(self.admin, "POST", "/admin/catalog", {"terms": [term], "rooms": [{**room, "seat_count": 15}]})
        self.assertEqual(self.call(self.admin, "GET", "/catalog/terms"), [{**term, "is_current": True}])
        second = {**term, "term_code": "2026-2027-2", "is_current": True}
        self.call(self.admin, "POST", "/admin/catalog", {"terms": [second]})
        self.assertEqual({item["term_code"]: item["is_current"]
                          for item in self.call(self.admin, "GET", "/catalog/terms")},
                         {TERM: False, "2026-2027-2": True})

    def test_catalog_reads_legacy_data_and_searches_literal_symbols(self):
        self.add_test_catalog()
        code = "中!_%\\"
        with connect(self.database) as cn, cn.cursor() as cur:
            cur.execute("""INSERT INTO course
                (course_code,course_name,department_code,credits,assessment,course_nature)
                VALUES (%s,'旧数据','CS',0,'ASSESSMENT','')""", (code,))
            cur.execute("INSERT INTO program (major_code,entry_year,required_credits) VALUES ('CS01',2026,3)")
        catalog = self.call(self.admin, "GET", "/admin/catalog/courses")["items"]
        self.assertEqual({row["course_code"] for row in catalog}, {"DB", code})
        for search in ("!", "_", "%", "\\"):
            response = self.admin.get(BASE + "/catalog/courses", params={"search": search})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([item["course_code"] for item in response.json()["data"]["items"]], [code])
        plan = self.call(self.admin, "GET", "/admin/programs?major_code=CS01&entry_year=2026")
        self.assertEqual(plan["courses"], [])

    def test_request_review_is_scoped_to_the_granted_kind(self):
        with connect(self.database) as cn, cn.cursor() as cur:
            reviewer_id = provision_admin(cn, "deferral-reviewer", ADMIN_PASSWORD)
            cur.execute("INSERT INTO role VALUES ('deferral-reviewer','缓考审核')")
            cur.execute("INSERT INTO role_permission VALUES ('deferral-reviewer','DEFERRAL_REVIEW')")
            cur.execute("UPDATE user_role SET role_code='deferral-reviewer' WHERE account_id=%s",
                        (reviewer_id,))
            cur.execute("INSERT INTO request (applicant_id,request_type,reason) VALUES (%s,'INFO_CHANGE','样例')",
                        (reviewer_id,))
            profile = cur.lastrowid
            cur.execute("INSERT INTO request (applicant_id,request_type,reason) VALUES (%s,'DEFERRAL','样例')",
                        (reviewer_id,))
            deferral = cur.lastrowid
        reviewer = self.client()
        self.login(reviewer, "deferral-reviewer", ADMIN_PASSWORD)
        queue = self.call(reviewer, "GET", "/admin/requests")
        self.assertEqual([item["request_id"] for item in queue["items"]], [deferral])
        self.call(reviewer, "GET", "/admin/requests?kind=INFO_CHANGE", status=403)
        self.call(reviewer, "GET", f"/admin/requests/{profile}", status=403)
        self.call(reviewer, "POST", f"/admin/requests/{profile}/decision", {"decision": "REJECT"}, status=403)
        self.assertEqual(self.call(reviewer, "GET", f"/admin/requests/{deferral}")["request_type"], "DEFERRAL")
        self.assertTrue(self.call(reviewer, "POST", f"/admin/requests/{deferral}/decision",
                                  {"decision": "REJECT"})["applied"])

    def test_assessment_grade_window_precision_and_late_return(self):
        catalog = self.add_test_catalog()
        self.call(self.admin, "POST", "/admin/catalog", {
            "courses": [{**catalog["courses"][0], "assessment": "ASSESSMENT"}],
        })
        alice, bob = self.add_students()
        teacher = self.add_teacher("teacher", "T01")
        self.call(self.admin, "PUT", "/admin/programs", {
            "major_code": "CS01", "entry_year": 2026, "required_credits": "3.00",
            "courses": [{"course_code": "DB", "curriculum_group": "核心",
                         "course_attribute": "REQUIRED", "suggested_term": 1}],
        })
        first, second = [self.call(self.admin, "POST", "/admin/sections", {
            "term_code": TERM, "class_no": "2026010001", "planned_term": 1, "course_code": "DB",
            "section_no": number, "offering_no": f"DB-{number}", "campus_code": "N", "capacity": 10,
            "teacher_no": "T01",
        }, status=201)["section_id"] for number in ("01", "02")]
        self.call(self.admin, "POST", f"/admin/sections/{first}/meetings", {
            "room_code": "R1", "day_of_week": 1, "first_period": 1, "last_period": 1, "weeks": [1],
        }, status=201)
        exam_error = self.call(self.admin, "POST", "/admin/exams", {
            "section_id": first, "term_code": TERM, "period": "FINAL", "kind": "REGULAR",
            "starts_at": "2026-11-23T02:00:00Z", "ends_at": "2026-11-23T03:00:00Z",
            "rooms": ["R1"], "invigilators": {"R1": "T01"}, "method": "机考",
        }, status=409)
        self.assertEqual(exam_error["code"], "NO_EXAM_REQUIRED")
        round_id = self.call(self.admin, "POST", "/admin/selection-rounds", {
            "term_code": TERM, "round_name": "正选", "mode": "DIRECT",
            "starts_at": "2026-11-23T00:30:00Z", "ends_at": "2026-11-23T01:15:00Z",
            "sections": [first],
        }, status=201)["round_id"]
        selection_path = f"/selection/rounds/{round_id}/sections/{first}/enrollment"
        enrolled = [self.call(student, "PUT", selection_path, {"action": "SELECT"})["enrollment_id"]
                    for student in (alice, bob)]
        window = {"term_code": TERM, "kind": "REGULAR", "opens_at": "2026-11-23T01:00:00.123456Z",
                  "closes_at": "2026-11-23T01:30:00.234567Z"}
        primary, _ = [self.call(self.admin, "POST", "/admin/grade-sheets",
                                {**window, "section_id": section}, status=201)["sheet_id"]
                      for section in (first, second)]
        scale = self.call(self.admin, "POST", "/admin/grade-scales", {
            "scale_code": "PERCENT", "version_no": 1, "scale_name": "百分制", "value_kind": "NUMERIC",
            "rules": [{"min_score": 0, "max_score": 100, "gpa": 4, "passed": True}],
        }, status=201)["scale_id"]
        path = f"/teacher/grade-sheets/{primary}"
        self.now = datetime(2026, 11, 23, 1, 5)
        self.call(teacher, "PUT", path + "/components", {"scale_id": scale, "weights": {"期末": "100"}})
        self.call(teacher, "PUT", path + "/entries", {"entries": [
            {"enrollment_id": enrollment, "scores": {"期末": "87"}} for enrollment in enrolled
        ]})
        self.call(bob, "PUT", selection_path, {"action": "DROP"})
        self.assertEqual([row["enrollment_id"] for row in self.call(teacher, "GET", path)["entries"]],
                         enrolled[:1])
        self.call(teacher, "POST", path + "/submit")
        self.now = datetime(2026, 11, 23, 1, 31)
        review_path = f"/admin/grade-sheets/{primary}/review"
        self.call(self.admin, "POST", review_path, {"decision": "RETURN", "opinion": "复核"})
        self.call(teacher, "PUT", path + "/entries", {"entries": [
            {"enrollment_id": enrolled[0], "scores": {"期末": "97"}},
        ]})
        self.call(teacher, "POST", path + "/submit")
        self.call(self.admin, "POST", review_path, {"decision": "PUBLISH"})
        self.assertEqual(self.call(alice, "GET", "/me/grades")[0]["total_score"], "97.00")
        self.assertEqual(self.call(bob, "GET", "/me/grades"), [])

    def test_frontend_contract_and_session_controls(self):
        doc = self.admin.get("/openapi.json").json()
        self.assertEqual(doc["components"]["securitySchemes"]["CourseSession"]["name"], "course_session")
        self.assertIn(BASE + "/selection/rounds/{round_id}/sections/{section_id}/enrollment", doc["paths"])
        params = doc["paths"][BASE + "/auth/password"]["patch"]["parameters"]
        self.assertTrue(any(p["name"] == "X-CSRF-Token" and p["required"] for p in params))
        self.assertEqual(self.call(self.admin, "GET", "/health"), {"status":"ok"})
        response = self.admin.options(BASE + "/auth/password", headers={
            "Origin":ORIGIN, "Access-Control-Request-Method":"PATCH",
            "Access-Control-Request-Headers":"content-type,x-csrf-token",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        bad_origin = self.call(self.admin,"PATCH","/auth/password",
                               {"old_password":ADMIN_PASSWORD,"new_password":"changed-test-password"},
                               origin="https://other.example", status=403)
        self.assertEqual(bad_origin["code"],"ORIGIN_DENIED")
        fresh=self.client()
        response=fresh.post(BASE + "/auth/login",json={"login_name":"admin","password":ADMIN_PASSWORD})
        cookies=response.headers["set-cookie"].lower()
        for flag in ("httponly","secure","samesite=strict"):
            self.assertIn(flag, cookies)
        data = {"old_password":ADMIN_PASSWORD,"new_password":"changed-test-password"}
        self.assertEqual(self.call(self.admin,"PATCH","/auth/password",data,csrf=False,status=403)["code"],
                         "INVALID_CSRF")
        self.assertTrue(self.call(self.admin,"PATCH","/auth/password",data)["applied"])
        self.assertEqual(self.call(self.admin,"GET","/auth/me",status=401)["code"],"UNAUTHENTICATED")
        self.call(fresh,"GET","/auth/me",status=401)
        self.login(self.admin,"admin","changed-test-password")
        self.call(self.admin,"DELETE","/auth/session")
        self.call(self.admin,"GET","/auth/me",status=401)

    def test_profile_request_scope_queue_and_account_revocation(self):
        self.add_test_catalog()
        alice,bob = self.add_students()
        bad = self.call(alice,"POST","/admin/accounts",
                        {"login_name":"forbidden","password":STUDENT_PASSWORD,"role":"admin"},status=403)
        self.assertEqual(bad["code"],"FORBIDDEN")
        profile = self.call(alice,"GET","/me/profile")
        self.assertIsNone(profile["phone"])
        first = self.call(alice,"POST","/me/profile-requests",{
            "changes":{"phone":"+1 222 555 0100","email":"sample@example.invalid"},"reason":"更新"},
            status=201)["request_id"]
        second = self.call(alice,"POST","/me/profile-requests",
                           {"changes":{"address":"样例地址"},"reason":"另一次申请"},status=201)["request_id"]
        queue = self.call(self.admin,"GET","/admin/requests?limit=1")
        self.assertEqual([row["request_id"] for row in queue["items"]],[first])
        next_page=self.admin.get(BASE+"/admin/requests",params={
            "limit":1,"after_requested_at":queue["after_requested_at"],
            "after_request_id":queue["after_request_id"],
        })
        self.assertEqual(next_page.status_code,200,next_page.text)
        self.assertEqual(next_page.json()["data"]["items"][0]["request_id"],second)
        self.assertEqual(self.call(bob,"GET","/me/requests")["items"],[])
        self.call(bob,"GET",f"/admin/requests/{first}",status=403)
        detail=self.call(self.admin,"GET",f"/admin/requests/{first}")
        self.assertEqual(detail["details"]["changes"][0]["new_value"],"+1 222 555 0100")
        self.assertTrue(self.call(self.admin,"POST",f"/admin/requests/{first}/decision",
                                 {"decision":"APPROVE","opinion":"核对通过"})["applied"])
        self.assertFalse(self.call(self.admin,"POST",f"/admin/requests/{first}/decision",
                                  {"decision":"APPROVE"})["applied"])
        self.call(self.admin,"POST",f"/admin/requests/{second}/decision",{"decision":"REJECT"})
        self.assertEqual(self.call(alice,"GET","/me/profile")["email"],"sample@example.invalid")
        self.assertIsNone(self.call(bob,"GET","/me/profile")["email"])
        with connect(self.database) as cn, cn.cursor() as cur:
            cur.execute("SELECT phone_cipher FROM student WHERE student_no='2026000001'")
            self.assertNotIn(b"+1 222 555 0100", bytes(cur.fetchone()[0]))
        me=self.call(alice,"GET","/auth/me")
        self.call(self.admin,"PATCH",f"/admin/accounts/{me['account_id']}/state",{"state":"LOCKED"})
        self.call(alice,"GET","/auth/me",status=401)

    def test_catalog_to_enrollment_exam_grades_and_evaluation(self):
        self.add_test_catalog()
        alice,bob=self.add_students()
        teacher=self.add_teacher("teacher","T01")
        other=self.add_teacher("other","T02")
        plan={
            "major_code":"CS01","entry_year":2026,"required_credits":"3.00",
            "courses":[{"course_code":"DB","curriculum_group":"核心","course_attribute":"REQUIRED",
                        "suggested_term":1}],
        }
        self.call(self.admin,"PUT","/admin/programs",plan)
        saved_plan = self.call(self.admin,"GET","/admin/programs?major_code=CS01&entry_year=2026")
        self.assertEqual(saved_plan["courses"],plan["courses"])
        self.assertEqual(self.call(alice,"GET","/me/program")["earned_credits"],"0")
        section=self.call(self.admin,"POST","/admin/sections",{
            "term_code":TERM,"class_no":"2026010001","planned_term":1,"course_code":"DB",
            "section_no":"01","offering_no":"DB-01","campus_code":"N","capacity":1,"teacher_no":"T01",
        },status=201)["section_id"]
        self.call(self.admin,"POST",f"/admin/sections/{section}/meetings",{
            "room_code":"R1","day_of_week":1,"first_period":1,"last_period":1,"weeks":[1,2],
        },status=201)
        round_id=self.call(self.admin,"POST","/admin/selection-rounds",{
            "term_code":TERM,"round_name":"正选","mode":"DIRECT","starts_at":"2026-11-23T08:00:00+08:00",
            "ends_at":"2026-11-23T10:00:00+08:00","sections":[section],"credit_limit":"3",
        },status=201)["round_id"]
        self.assertTrue(self.call(alice,"GET",f"/selection/rounds?term={TERM}")[0]["is_open"])
        self.assertEqual(self.call(self.admin,"GET",f"/admin/selection-rounds?term={TERM}")["items"][0]["round_id"],
                         round_id)
        enroll_path=f"/selection/rounds/{round_id}/sections/{section}/enrollment"
        enrollment=self.call(alice,"PUT",enroll_path,{"action":"SELECT"})
        self.assertEqual(self.call(alice,"PUT",enroll_path,{"action":"SELECT"}),enrollment)
        self.assertEqual(self.call(bob,"PUT",enroll_path,{"action":"SELECT"},status=409)["code"],"FULL")
        self.assertEqual(self.call(alice,"GET",f"/me/timetable?term={TERM}&week=1")[0]["section_id"],section)
        self.assertEqual(self.call(teacher,"GET",f"/me/timetable?term={TERM}&week=1")[0]["section_id"],section)
        self.assertEqual(self.call(teacher,"GET",f"/teacher/sections/{section}/roster")[0]["enrollment_id"],
                         enrollment["enrollment_id"])
        self.call(other,"GET",f"/teacher/sections/{section}/roster",status=403)

        exam=self.call(self.admin,"POST","/admin/exams",{
            "section_id":section,"term_code":TERM,"period":"FINAL","kind":"REGULAR",
            "starts_at":"2026-11-23T02:00:00Z","ends_at":"2026-11-23T03:00:00Z",
            "rooms":["R1"],"invigilators":{"R1":"T01"},"method":"机考",
        },status=201)["exam_id"]
        seat=self.call(alice,"GET",f"/me/exams?term={TERM}")[0]
        self.assertEqual(self.call(self.admin,"GET",f"/admin/exams?term={TERM}")["items"][0]["exam_id"],exam)
        self.assertEqual(seat["exam_method"],"机考")
        self.assertEqual(seat["starts_at"],"2026-11-23T02:00:00Z")
        self.assertEqual(self.call(bob,"GET",f"/me/exams?term={TERM}"),[])
        self.assertEqual(self.call(teacher,"GET",f"/teacher/invigilation?term={TERM}")[0]["exam_id"],exam)
        self.call(bob,"POST","/me/deferral-requests",
                  {"exam_seat_id":seat["exam_seat_id"],"evidence_ref":"test-proof","reason":"样例"},status=403)

        scale=self.call(self.admin,"POST","/admin/grade-scales",{
            "scale_code":"PERCENT","version_no":1,"scale_name":"百分制","value_kind":"NUMERIC",
            "rules":[{"min_score":0,"max_score":"59.99","gpa":0,"passed":False},
                     {"min_score":60,"max_score":100,"gpa":4,"passed":True}],
        },status=201)["scale_id"]
        sheet=self.call(self.admin,"POST","/admin/grade-sheets",{
            "section_id":section,"term_code":TERM,"kind":"REGULAR","exam_id":exam,
            "opens_at":"2026-11-23T03:30:00Z","closes_at":"2026-11-23T07:00:00Z",
        },status=201)["sheet_id"]
        self.now=datetime(2026,11,23,4)
        self.call(teacher,"PUT",f"/teacher/grade-sheets/{sheet}/components",
                  {"scale_id":scale,"weights":{"平时":"30","期末":"70"}})
        self.call(teacher,"PUT",f"/teacher/grade-sheets/{sheet}/entries",
                  {"entries":[{"enrollment_id":enrollment["enrollment_id"],"scores":{"平时":"80","期末":"90"}}]})
        self.call(other,"GET",f"/teacher/grade-sheets/{sheet}",status=403)
        self.assertEqual(self.call(teacher,"GET",f"/teacher/grade-sheets/{sheet}")["entries"][0]["total_score"],
                         "87.00")
        self.call(teacher,"POST",f"/teacher/grade-sheets/{sheet}/submit")
        self.assertEqual(self.call(alice,"GET","/me/grades"),[])
        self.call(self.admin,"POST",f"/admin/grade-sheets/{sheet}/review",{"decision":"PUBLISH"})
        published=self.call(alice,"GET",f"/me/grades?term={TERM}&mode=best")[0]
        self.assertEqual(published["total_score"],"87.00")
        self.assertEqual(self.call(bob,"GET","/me/grades"),[])
        request_id=self.call(teacher,"POST",f"/teacher/grades/{published['grade_id']}/correction-requests",
                             {"scores":{"平时":"90","期末":"100"},"reason":"复核"},status=201)["request_id"]
        self.call(self.admin,"POST",f"/admin/requests/{request_id}/decision",{"decision":"APPROVE"})
        self.assertEqual(self.call(alice,"GET","/me/grades")[0]["total_score"],"97.00")
        self.assertEqual(self.call(alice,"GET","/me/program")["earned_credits"],"3.00")

        batch=self.call(self.admin,"POST","/admin/evaluation-batches",{
            "term_code":TERM,"name":"课堂质量","category":"期末","starts_at":"2026-11-23T03:30:00Z",
            "ends_at":"2026-11-23T05:00:00Z","indicators":[{"name":"教学","weight":"100","maximum":"10"}],
        },status=201)["batch_id"]
        self.assertEqual(self.call(teacher,"GET",f"/evaluation-batches?term={TERM}")["items"][0]["batch_id"],batch)
        self.assertEqual(self.call(other,"GET",f"/evaluation-batches?term={TERM}")["items"],[])
        task=self.call(alice,"GET",f"/me/evaluations?term={TERM}")[0]
        answer={"scores":{str(task["indicators"][0]["indicator_id"]):"9"},"comment":"仅存于评价记录"}
        target=f"/me/evaluations/{task['assignment_id']}/submission"
        self.call(bob,"POST",target,answer,status=404)
        self.assertEqual(self.call(alice,"POST",target,answer)["total_score"],"90.00")
        self.assertEqual(self.call(alice,"POST",target,answer,status=409)["code"],"EVAL_ALREADY_SUBMITTED")
        self.now=datetime(2026,11,23,5,30)
        self.call(self.admin,"POST",f"/admin/evaluation-batches/{batch}/finish")
        results=self.call(teacher,"GET",f"/teacher/evaluation-results?batch_id={batch}")
        self.assertEqual(results[0]["average_score"],"90.00")
        self.assertFalse({"student_no","comment","assignment_id"} & results[0].keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)
