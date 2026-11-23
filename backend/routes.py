"""FastAPI routes for the versioned teaching-system interface."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from secrets import token_bytes
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Response
from pydantic import AwareDatetime

from . import academic, accounts, evaluations, exams, grades, models as m, people, queries
from .database import require, rows, value
from .errors import ERROR_RESPONSES, Fault
from .security import (
    COOKIE, COOKIE_PATH, Actor, Connection, account_view, end_session, has_permission,
    set_session_cookie, start_session,
)


router = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)
PathId = Annotated[int, Path(gt=0, le=9223372036854775807)]
TermQuery = Annotated[str, Query(pattern=r"^[0-9]{4}-[0-9]{4}-[1-3]$")]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=1000000)]
Week = Annotated[int, Query(ge=1, le=30)]
CatalogKind = Literal["departments", "majors", "classes", "terms", "campuses",
                      "period_times", "teachers", "rooms", "courses"]
CatalogItem = (m.DepartmentView | m.MajorView | m.AdministrativeClassView | m.TermView | m.CampusView
               | m.PeriodTimeView | m.TeacherView | m.RoomView | m.CourseView)
CATALOG_OUTPUT = {
    "departments": m.DepartmentView, "majors": m.MajorView, "classes": m.AdministrativeClassView,
    "terms": m.TermView, "campuses": m.CampusView, "period_times": m.PeriodTimeView,
    "teachers": m.TeacherView, "rooms": m.RoomView, "courses": m.CourseView,
}


def as_json(data):
    if isinstance(data, dict):
        return {k: as_json(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [as_json(item) for item in data]
    if isinstance(data, datetime) and data.tzinfo is None:
        return data.replace(tzinfo=timezone.utc)
    if isinstance(data, Decimal):
        return str(data)
    if isinstance(data, timedelta):
        seconds = int(data.total_seconds())
        return f"{seconds//3600:02d}:{seconds%3600//60:02d}:{seconds%60:02d}"
    return data


def result(data):
    return {"data": as_json(data)}


def page(items, limit, offset):
    return result({"items": items, "limit": limit, "offset": offset})


def done(applied=True):
    return result({"applied": applied})


def actor_id(actor):
    return actor["account_id"]


def clock(request):
    return request.app.state.clock()


@router.get("/health", response_model=m.Envelope[m.Health], tags=["运行状态"])
def health(cn: Connection):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        value(cur, "SELECT 1")
    return result({"status": "ok"})


@router.post("/auth/login", response_model=m.Envelope[m.Session], tags=["会话"])
def login(data: m.Login, request: Request, response: Response, cn: Connection):
    request.app.state.login_gate.observe(request.client.host if request.client else "unknown", data.login_name)
    token, actor = start_session(cn, data.login_name, data.password.get_secret_value(), clock(request))
    set_session_cookie(response, request, token)
    return result(actor)


@router.get("/auth/me", response_model=m.Envelope[m.Session], tags=["会话"])
def session(actor: Actor):
    return result(actor)


@router.delete("/auth/session", response_model=m.Envelope[m.Done], tags=["会话"])
def logout(request: Request, response: Response, cn: Connection, actor: Actor):
    end_session(cn, request.cookies[COOKIE])
    response.delete_cookie(COOKIE, COOKIE_PATH, secure=request.app.state.settings.secure_cookie,
                           httponly=True, samesite="strict")
    return done()


@router.patch("/auth/password", response_model=m.Envelope[m.Done], tags=["会话"])
def update_password(data: m.ChangePassword, cn: Connection, actor: Actor):
    accounts.change_password(cn, actor_id(actor), data.old_password.get_secret_value(),
                             data.new_password.get_secret_value(), revoke_sessions=True)
    return done()


@router.get("/admin/accounts", response_model=m.Envelope[m.Page[m.Account]], tags=["账户"])
def account_list(cn: Connection, actor: Actor, limit: Limit = 50, offset: Offset = 0):
    has_permission(cn, actor, "ACCOUNT_ADMIN")
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(
            cur,
            """SELECT a.account_id,a.login_name,a.student_no,a.teacher_no,a.account_state,
                      (SELECT GROUP_CONCAT(r.role_code ORDER BY r.role_code) FROM user_role r
                        WHERE r.account_id=a.account_id) AS role_codes
               FROM user_account a ORDER BY a.account_id LIMIT %s OFFSET %s""",
            (limit, offset),
        )
    for item in items:
        codes = item.pop("role_codes")
        item["roles"] = codes.split(",") if codes else []
    return page(items, limit, offset)


@router.post("/admin/accounts", response_model=m.Envelope[m.Account], status_code=201, tags=["账户"])
def add_account(data: m.AccountCreate, cn: Connection, actor: Actor):
    new_id = accounts.create_account(
        cn, actor_id(actor), data.login_name, data.password.get_secret_value(),
        student_no=data.student_no, teacher_no=data.teacher_no, role=data.role,
    )
    with cn.cursor(dictionary=True, buffered=True) as cur:
        return result(account_view(cur, new_id))


@router.patch("/admin/accounts/{account_id}/state", response_model=m.Envelope[m.Done], tags=["账户"])
def change_account_state(account_id: PathId, data: m.AccountState, cn: Connection, actor: Actor):
    return done(people.update_account_state(cn, actor_id(actor), account_id, data.state))


@router.put("/admin/accounts/{account_id}/roles/{role}", response_model=m.Envelope[m.Done], tags=["账户"])
def change_role(account_id: PathId, role: m.Role, data: m.ToggleRole, cn: Connection, actor: Actor):
    accounts.set_role(cn, actor_id(actor), account_id, role, enabled=data.enabled)
    return done()


@router.get("/catalog/terms", response_model=m.Envelope[list[m.TermView]], tags=["基础数据"])
def term_list(cn: Connection, actor: Actor):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        return result(rows(cur, """SELECT term_code,starts_on,week_count,is_current FROM term
                                 ORDER BY starts_on DESC,term_code DESC"""))


@router.get("/catalog/courses", response_model=m.Envelope[m.Page[m.CourseView]], tags=["基础数据"])
def course_list(cn: Connection, actor: Actor, search: Annotated[str, Query(max_length=100)] = "",
                limit: Limit = 50, offset: Offset = 0):
    key = "%" + search.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(cur, """SELECT * FROM course WHERE is_active
                             AND (course_code LIKE %s ESCAPE '!' OR course_name LIKE %s ESCAPE '!')
                             ORDER BY course_code LIMIT %s OFFSET %s""", (key, key, limit, offset))
    return page(items, limit, offset)


@router.get("/admin/catalog/{kind}", response_model=m.Envelope[m.Page[CatalogItem]], tags=["基础数据"])
def admin_catalog_items(kind: CatalogKind, cn: Connection, actor: Actor,
                        limit: Limit = 50, offset: Offset = 0):
    model = CATALOG_OUTPUT[kind]
    items = queries.catalog_items(cn, actor_id(actor), kind, limit, offset)
    return page([{key: row[key] for key in model.model_fields} for row in items], limit, offset)


@router.post("/admin/catalog", response_model=m.Envelope[m.Done], tags=["基础数据"])
def write_catalog(data: m.Catalog, request: Request, cn: Connection, actor: Actor):
    people.update_catalog(cn, actor_id(actor), data, clock(request))
    return done()


@router.put("/admin/programs", response_model=m.Envelope[m.ProgramId], tags=["培养方案"])
def write_program(data: m.Program, cn: Connection, actor: Actor):
    return result({"program_id": people.save_program(cn, actor_id(actor), data)})


@router.get("/me/program", response_model=m.Envelope[m.ProgramView], tags=["培养方案"])
def my_program(cn: Connection, actor: Actor):
    return result(people.student_program(cn, actor_id(actor)))


@router.post("/admin/student-imports", response_model=m.Envelope[m.ImportResult], tags=["学籍"])
def import_students(data: m.StudentImport, cn: Connection, actor: Actor):
    students = [(s.student_no, s.student_name, s.class_no, s.admitted_on) for s in data.students]
    batch, created = academic.import_students(cn, actor_id(actor), data.batch_no, students)
    return result({"batch_id": batch, "created": created})


@router.get("/me/profile", response_model=m.Envelope[m.Profile], tags=["学籍"])
def my_profile(request: Request, cn: Connection, actor: Actor):
    return result(people.profile(cn, actor_id(actor), request.app.state.settings.data_key))


@router.post("/me/profile-requests", response_model=m.Envelope[m.RequestId], status_code=201, tags=["学籍"])
def profile_request(data: m.ProfileRequest, request: Request, cn: Connection, actor: Actor):
    request_id = people.submit_profile(
        cn, actor_id(actor), data.changes.model_dump(exclude_unset=True), data.reason,
        request.app.state.settings.data_key, clock(request),
    )
    return result({"request_id": request_id})


@router.get("/me/requests", response_model=m.Envelope[m.Page[m.RequestSummary]], tags=["申请"])
def my_requests(cn: Connection, actor: Actor, limit: Limit = 50, offset: Offset = 0):
    return page(queries.requests_for(cn, actor_id(actor), limit, offset), limit, offset)


@router.get("/admin/requests", response_model=m.Envelope[m.RequestQueue], tags=["申请"])
def admin_request_queue(
    cn: Connection, actor: Actor, state: m.RequestState = "PENDING", kind: m.RequestKind | None = None,
    after_requested_at: AwareDatetime | None = None,
    after_request_id: Annotated[int | None, Query(gt=0, le=9223372036854775807)] = None,
    limit: Limit = 50,
):
    if (after_requested_at is None) != (after_request_id is None):
        raise Fault(400, "INVALID_CURSOR", "请同时提交两个游标字段")
    cursor_time = m.utc_naive(after_requested_at) if after_requested_at is not None else None
    return result(queries.request_queue(cn, actor_id(actor), state, kind, cursor_time, after_request_id, limit))


@router.get("/admin/requests/{request_id}", response_model=m.Envelope[m.RequestDetail], tags=["申请"])
def admin_request_detail(request_id: PathId, request: Request, cn: Connection, actor: Actor):
    return result(people.request_detail(cn, actor_id(actor), request_id, request.app.state.settings.data_key))


@router.post("/admin/requests/{request_id}/decision", response_model=m.Envelope[m.Done], tags=["申请"])
def review_request(request_id: PathId, data: m.Review, request: Request, cn: Connection, actor: Actor):
    return done(people.decide_request(
        cn, actor_id(actor), request_id, data.decision, data.opinion, request.app.state.settings.data_key,
        clock(request),
    ))


@router.get("/admin/sections", response_model=m.Envelope[m.Page[m.Section]], tags=["开课排课"])
def admin_sections(term: TermQuery, cn: Connection, actor: Actor, limit: Limit = 50, offset: Offset = 0):
    return page(queries.sections(cn, actor_id(actor), term, limit, offset), limit, offset)


@router.post("/admin/sections", response_model=m.Envelope[m.SectionId], status_code=201, tags=["开课排课"])
def add_section(data: m.SectionCreate, cn: Connection, actor: Actor):
    number = academic.create_offering(
        cn, actor_id(actor), data.term_code, data.class_no, data.planned_term, data.course_code,
        data.section_no, data.offering_no, data.campus_code, data.capacity, data.teacher_no,
    )
    return result({"section_id": number})


@router.post("/admin/sections/{section_id}/meetings", response_model=m.Envelope[m.MeetingId],
             status_code=201, tags=["开课排课"])
def add_meeting(section_id: PathId, data: m.Meeting, cn: Connection, actor: Actor):
    schedule_id = academic.schedule_section(
        cn, actor_id(actor), section_id, data.room_code, data.day_of_week,
        (data.first_period,data.last_period), set(data.weeks),
    )
    return result({"schedule_id": schedule_id})


@router.post("/admin/selection-rounds", response_model=m.Envelope[m.RoundId],
             status_code=201, tags=["选课"])
def add_round(data: m.RoundCreate, cn: Connection, actor: Actor):
    draw_seed = token_bytes(32) if data.mode == "LOTTERY" else None
    round_id = academic.open_round(
        cn, actor_id(actor), data.term_code, data.round_name, data.mode,
        data.starts_at, data.ends_at, data.sections, data.credit_limit, data.classes, seed=draw_seed,
    )
    return result({"round_id": round_id})


@router.post("/admin/selection-rounds/{round_id}/settle", response_model=m.Envelope[m.Settlement], tags=["选课"])
def settle_round(round_id: PathId, request: Request, cn: Connection, actor: Actor):
    academic.close_lottery(cn, actor_id(actor), round_id, clock(request), seed=token_bytes(32))
    return result(queries.lottery_counts(cn, actor_id(actor), round_id))


@router.post("/admin/selection-rounds/{round_id}/adjustments", response_model=m.Envelope[m.Enrollment], tags=["选课"])
def adjust_round(round_id: PathId, data: m.Adjustment, request: Request, cn: Connection, actor: Actor):
    enrollment_id, state = academic.adjust_selection(
        cn, actor_id(actor), round_id, data.student_no, data.section_id, clock(request),
        drop=data.action == "DROP", reason=data.reason,
    )
    return result({"enrollment_id": enrollment_id, "state": state})


@router.get("/selection/rounds", response_model=m.Envelope[list[m.RoundView]], tags=["选课"])
def list_rounds(term: TermQuery, request: Request, cn: Connection, actor: Actor):
    return result(queries.selection_rounds(cn, actor_id(actor), term, clock(request)))


@router.get("/selection/rounds/{round_id}/sections", response_model=m.Envelope[m.Page[m.RoundSection]], tags=["选课"])
def list_round_sections(round_id: PathId, cn: Connection, actor: Actor, limit: Limit = 50, offset: Offset = 0):
    return page(queries.round_sections(cn, actor_id(actor), round_id, limit, offset), limit, offset)


@router.put("/selection/rounds/{round_id}/sections/{section_id}/enrollment",
            response_model=m.Envelope[m.Enrollment], tags=["选课"])
def select_section(
    round_id: PathId, section_id: PathId, data: m.SelectionAction,
    request: Request, cn: Connection, actor: Actor,
):
    enrollment_id, state = academic.change_selection(
        cn, actor_id(actor), round_id, section_id, clock(request), drop=data.action == "DROP",
    )
    return result({"enrollment_id": enrollment_id, "state": state})


@router.get("/me/selections", response_model=m.Envelope[list[m.StudentSelection]], tags=["选课"])
def my_selections(term: TermQuery, cn: Connection, actor: Actor):
    return result(academic.student_selections(cn, actor_id(actor), term))


@router.get("/me/timetable", response_model=m.Envelope[list[m.TimetableItem]], tags=["开课排课"])
def my_timetable(term: TermQuery, week: Week, cn: Connection, actor: Actor):
    load_timetable = queries.teacher_timetable if "teacher" in actor["roles"] else academic.student_timetable
    return result(load_timetable(cn, actor_id(actor), term, week))


@router.get("/teacher/sections", response_model=m.Envelope[m.Page[m.Section]], tags=["开课排课"])
def teacher_sections(term: TermQuery, cn: Connection, actor: Actor, limit: Limit = 50, offset: Offset = 0):
    return page(queries.sections(cn, actor_id(actor), term, limit, offset, teacher=True), limit, offset)


@router.get("/teacher/sections/{section_id}/roster", response_model=m.Envelope[list[m.RosterItem]], tags=["开课排课"])
def section_roster(section_id: PathId, cn: Connection, actor: Actor):
    return result(queries.roster(cn, actor_id(actor), section_id))


@router.post("/admin/exams", response_model=m.Envelope[m.ExamId], status_code=201, tags=["考试"])
def add_exam(data: m.ExamCreate, cn: Connection, actor: Actor):
    exam_id = exams.publish_exam(
        cn, actor_id(actor), data.section_id, data.term_code, data.period, data.kind,
        data.starts_at, data.ends_at, data.rooms, invigilators=data.invigilators,
        attempt=data.attempt, method=data.method,
    )
    return result({"exam_id": exam_id})


@router.get("/me/exams", response_model=m.Envelope[list[m.ExamSeat]], tags=["考试"])
def my_exams(term: TermQuery, cn: Connection, actor: Actor):
    return result(exams.student_exams(cn, actor_id(actor), term))


@router.get("/teacher/invigilation", response_model=m.Envelope[list[m.Invigilation]], tags=["考试"])
def invigilation(term: TermQuery, cn: Connection, actor: Actor):
    return result(exams.teacher_invigilation(cn, actor_id(actor), term))


@router.post("/me/deferral-requests", response_model=m.Envelope[m.RequestId], status_code=201, tags=["考试"])
def create_deferral(data: m.Deferral, request: Request, cn: Connection, actor: Actor):
    return result({"request_id": exams.request_deferral(
        cn, actor_id(actor), data.exam_seat_id, data.evidence_ref, clock(request), reason=data.reason,
    )})


@router.post("/admin/grade-scales", response_model=m.Envelope[m.ScaleId], status_code=201, tags=["成绩"])
def create_scale(data: m.GradeScale, cn: Connection, actor: Actor):
    return result({"scale_id": queries.publish_scale(cn, actor_id(actor), data)})


@router.get("/grade-scales", response_model=m.Envelope[list[m.ScaleView]], tags=["成绩"])
def list_scales(cn: Connection, actor: Actor):
    return result(queries.grade_scales(cn, actor_id(actor), actor["roles"]))


@router.get("/admin/grade-sheets", response_model=m.Envelope[m.Page[m.SheetView]], tags=["成绩"])
def admin_sheets(term: TermQuery, cn: Connection, actor: Actor, state: m.SheetState | None = None,
                 limit: Limit = 50, offset: Offset = 0):
    return page(queries.sheets(cn, actor_id(actor), term, state, limit, offset), limit, offset)


@router.post("/admin/grade-sheets", response_model=m.Envelope[m.SheetId], status_code=201, tags=["成绩"])
def add_sheet(data: m.GradeSheetCreate, cn: Connection, actor: Actor):
    sheet_id = grades.open_grade_sheet(
        cn, actor_id(actor), data.section_id, data.term_code, data.kind, data.opens_at, data.closes_at,
        exam_id=data.exam_id,
    )
    return result({"sheet_id": sheet_id})


@router.get("/admin/grade-sheets/{sheet_id}", response_model=m.Envelope[m.SheetDetail], tags=["成绩"])
def admin_sheet(sheet_id: PathId, cn: Connection, actor: Actor):
    return result(queries.sheet_detail(cn, actor_id(actor), sheet_id))


@router.get("/teacher/grade-sheets", response_model=m.Envelope[m.Page[m.SheetView]], tags=["成绩"])
def teacher_sheets(term: TermQuery, cn: Connection, actor: Actor, state: m.SheetState | None = None,
                   limit: Limit = 50, offset: Offset = 0):
    return page(queries.sheets(cn, actor_id(actor), term, state, limit, offset, teacher=True), limit, offset)


@router.get("/teacher/grade-sheets/{sheet_id}", response_model=m.Envelope[m.SheetDetail], tags=["成绩"])
def teacher_sheet(sheet_id: PathId, cn: Connection, actor: Actor):
    return result(queries.sheet_detail(cn, actor_id(actor), sheet_id, teacher=True))


@router.put("/teacher/grade-sheets/{sheet_id}/components", response_model=m.Envelope[m.Done], tags=["成绩"])
def configure_sheet(sheet_id: PathId, data: m.GradeComponents, request: Request, cn: Connection, actor: Actor):
    grades.configure_grades(cn, actor_id(actor), sheet_id, data.scale_id, data.weights, clock(request))
    return done()


@router.put("/teacher/grade-sheets/{sheet_id}/entries", response_model=m.Envelope[m.WrittenGrades], tags=["成绩"])
def save_entries(sheet_id: PathId, data: m.GradeEntries, request: Request, cn: Connection, actor: Actor):
    require(len({item.enrollment_id for item in data.entries}) == len(data.entries), "INVALID_DUPLICATE_ENTRIES")
    entries = [(item.enrollment_id, item.scores, item.level_code, item.flag) for item in data.entries]
    saved = grades.save_grades(cn, actor_id(actor), sheet_id, entries, clock(request))
    return result({"grades": saved})


@router.post("/teacher/grade-sheets/{sheet_id}/submit", response_model=m.Envelope[m.Done], tags=["成绩"])
def submit_sheet(sheet_id: PathId, request: Request, cn: Connection, actor: Actor):
    grades.submit_grades(cn, actor_id(actor), sheet_id, clock(request))
    return done()


@router.post("/admin/grade-sheets/{sheet_id}/review", response_model=m.Envelope[m.Done], tags=["成绩"])
def review_sheet(sheet_id: PathId, data: m.SheetReview, request: Request, cn: Connection, actor: Actor):
    grades.review_sheet(cn, actor_id(actor), sheet_id, data.decision == "PUBLISH", clock(request), data.opinion)
    return done()


@router.get("/me/grades", response_model=m.Envelope[list[m.GradeView]], tags=["成绩"])
def my_grades(cn: Connection, actor: Actor,
              term: Annotated[str | None, Query(pattern=r"^[0-9]{4}-[0-9]{4}-[1-3]$")] = None,
              mode: Literal["all","best"] = "all"):
    return result(grades.published_grades(cn, actor_id(actor), best=mode == "best", term=term))


@router.post("/teacher/grades/{grade_id}/correction-requests", response_model=m.Envelope[m.RequestId],
             status_code=201, tags=["成绩"])
def request_correction(grade_id: PathId, data: m.Correction, cn: Connection, actor: Actor):
    return result({"request_id": grades.request_correction(cn, actor_id(actor), grade_id, data.scores, data.reason)})


@router.post("/admin/evaluation-batches", response_model=m.Envelope[m.BatchId], status_code=201, tags=["评教"])
def add_evaluation(data: m.EvaluationBatch, cn: Connection, actor: Actor):
    indicators = [(i.name, i.weight, i.maximum) for i in data.indicators]
    batch = evaluations.open_evaluation(
        cn, actor_id(actor), data.term_code, data.starts_at, data.ends_at,
        indicators, name=data.name, category=data.category,
    )
    return result({"batch_id": batch})


@router.post("/admin/evaluation-batches/{batch_id}/finish", response_model=m.Envelope[m.Done], tags=["评教"])
def finish_evaluation(batch_id: PathId, request: Request, cn: Connection, actor: Actor):
    return done(evaluations.finish_evaluation(cn, actor_id(actor), batch_id, clock(request)))


@router.get("/me/evaluations", response_model=m.Envelope[list[m.EvaluationTask]], tags=["评教"])
def my_evaluations(term: TermQuery, cn: Connection, actor: Actor):
    return result(queries.evaluation_tasks(cn, actor_id(actor), term))


@router.post("/me/evaluations/{assignment_id}/submission", response_model=m.Envelope[m.EvaluationScore], tags=["评教"])
def submit_evaluation(assignment_id: PathId, data: m.EvaluationSubmission,
                      request: Request, cn: Connection, actor: Actor):
    has_permission(cn, actor, "EVAL_SUBMIT")
    with cn.cursor(dictionary=True, buffered=True) as cur:
        batch_id = value(cur, """SELECT t.batch_id FROM eval_assignment a
                                 JOIN eval_target t ON t.target_id=a.target_id
                                 JOIN enrollment e ON e.enrollment_id=a.enrollment_id
                                 WHERE a.assignment_id=%s AND e.student_no=%s""",
                         (assignment_id, actor["student_no"]))
        require(batch_id, "NO_EVAL_ASSIGNMENT")
    total = evaluations.submit_evaluation(
        cn, actor_id(actor), batch_id, assignment_id, data.scores, clock(request), comment=data.comment,
    )
    return result({"total_score": total})


@router.get("/teacher/evaluation-results", response_model=m.Envelope[list[m.EvaluationResult]], tags=["评教"])
def teacher_evaluation_results(batch_id: Annotated[int, Query(gt=0)], cn: Connection, actor: Actor):
    return result(evaluations.teacher_results(cn, actor_id(actor), batch_id, 1))


@router.get("/admin/evaluation-results", response_model=m.Envelope[list[m.EvaluationResult]], tags=["评教"])
def admin_evaluation_results(batch_id: Annotated[int, Query(gt=0)], cn: Connection, actor: Actor):
    return result(queries.evaluation_results(cn, actor_id(actor), batch_id))


@router.get("/admin/students", response_model=m.Envelope[m.Page[m.StudentSummary]], tags=["学籍"])
def student_list(cn: Connection, actor: Actor,
                 class_no: Annotated[str | None, Query(pattern=r"^[0-9]{10}$")] = None,
                 limit: Limit = 50, offset: Offset = 0):
    has_permission(cn, actor, "ADMISSION")
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(cur, """SELECT student_no,student_name,class_no,academic_state FROM student
                            WHERE (%s IS NULL OR class_no=%s)
                            ORDER BY student_no LIMIT %s OFFSET %s""", (class_no, class_no, limit, offset))
    return page(items, limit, offset)


@router.get("/admin/programs", response_model=m.Envelope[m.AdminProgram], tags=["培养方案"])
def admin_program(cn: Connection, actor: Actor,
                  major_code: Annotated[str, Query(min_length=1, max_length=16)],
                  entry_year: Annotated[int, Query(ge=1900, le=2200)]):
    has_permission(cn, actor, "CATALOG")
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(cur, """SELECT program_id,major_code,entry_year,required_credits FROM program
                             WHERE major_code=%s AND entry_year=%s""", (major_code, entry_year))
        require(items, "NO_PROGRAM")
        plan = items[0]
        plan["courses"] = rows(cur, """SELECT course_code,curriculum_group,course_attribute,suggested_term
                                      FROM program_course WHERE program_id=%s
                                      ORDER BY suggested_term,course_code""", (plan["program_id"],))
    return result(plan)


@router.get("/admin/selection-rounds", response_model=m.Envelope[m.Page[m.RoundView]], tags=["选课"])
def admin_rounds(term: TermQuery, request: Request, cn: Connection, actor: Actor,
                 limit: Limit = 50, offset: Offset = 0):
    has_permission(cn, actor, "ROUND")
    now = clock(request)
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(cur, """SELECT round_id,term_code,round_name,mode,state,starts_at,ends_at,credit_limit,
                                    (state='OPEN' AND starts_at<=%s AND ends_at>%s) AS is_open
                             FROM selection_round WHERE term_code=%s ORDER BY starts_at,round_id
                             LIMIT %s OFFSET %s""", (now, now, term, limit, offset))
    return page(items, limit, offset)


@router.get("/admin/exams", response_model=m.Envelope[m.Page[m.ExamView]], tags=["考试"])
def admin_exams(term: TermQuery, cn: Connection, actor: Actor, limit: Limit = 50, offset: Offset = 0):
    has_permission(cn, actor, "EXAM")
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(cur, """SELECT e.exam_id,e.section_id,c.course_name,e.scheduled_term_code,
                                   e.exam_period,e.attempt_kind,e.attempt_no,e.starts_at,
                                   e.ends_at,e.exam_method,e.state
                            FROM exam e JOIN section s ON s.section_id=e.section_id
                            JOIN course c ON c.course_code=s.course_code
                            WHERE e.scheduled_term_code=%s ORDER BY e.starts_at,e.exam_id LIMIT %s OFFSET %s""",
                     (term, limit, offset))
    return page(items, limit, offset)


@router.get("/evaluation-batches", response_model=m.Envelope[m.Page[m.EvaluationBatchView]], tags=["评教"])
def evaluation_batches(term: TermQuery, cn: Connection, actor: Actor,
                       limit: Limit = 50, offset: Offset = 0):
    administrator = "admin" in actor["roles"]
    identity = has_permission(cn, actor, "EVAL_ADMIN" if administrator else "EVAL_READ")
    scope = "" if administrator else (
        " AND EXISTS (SELECT 1 FROM eval_target t WHERE t.batch_id=b.batch_id AND t.teacher_no=%s)"
    )
    args = (term, limit, offset) if administrator else (term, identity["teacher_no"], limit, offset)
    with cn.cursor(dictionary=True, buffered=True) as cur:
        items = rows(cur, f"""SELECT b.batch_id,b.term_code,b.batch_name,b.category,b.opens_at,b.closes_at,b.state
                             FROM eval_batch b WHERE b.term_code=%s{scope}
                             ORDER BY b.opens_at,b.batch_id LIMIT %s OFFSET %s""", args)
    return page(items, limit, offset)
