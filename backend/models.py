"""Public request and response models for API v1."""

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, StringConstraints


def utc_naive(value: datetime) -> datetime:
    utc = value.astimezone(timezone.utc)
    return utc.replace(tzinfo=None, microsecond=utc.microsecond // 1000 * 1000)


Id = Annotated[int, Field(gt=0, le=9223372036854775807)]
Code = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")]
DepartmentCode = Annotated[Code, Field(max_length=10)]
MajorCode = Annotated[Code, Field(max_length=16)]
CampusCode = Annotated[Code, Field(max_length=10)]
RoomCode = Annotated[str, Field(min_length=1, max_length=24)]
StudentNo = Annotated[str, Field(pattern=r"^[0-9]{10}$")]
TermCode = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{4}-[1-3]$")]
At = Annotated[AwareDatetime, AfterValidator(utc_naive)]
Text40 = Annotated[str, Field(min_length=1, max_length=40)]
Text80 = Annotated[str, Field(min_length=1, max_length=80)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
Password = Annotated[SecretStr, Field(min_length=1, max_length=128)]
NewPassword = Annotated[SecretStr, Field(min_length=12, max_length=128)]
Credit = Annotated[Decimal, Field(ge=0, le=99.99, decimal_places=2)]
Score = Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)]
Period = Annotated[int, Field(ge=1, le=14)]
Role = Literal["admin", "student", "teacher"]
Attempt = Literal["REGULAR", "MAKEUP", "DEFERRED"]
RequestKind = Literal["INFO_CHANGE", "DEFERRAL", "GRADE_CORRECTION"]
RequestState = Literal["PENDING", "IN_REVIEW", "APPROVED", "REJECTED", "WITHDRAWN"]
SheetState = Literal["EMPTY", "DRAFT", "SUBMITTED", "PUBLISHED"]
EnrollmentState = Literal["PENDING", "ENROLLED", "REJECTED", "DROPPED", "COMPLETED"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T


class Page(BaseModel, Generic[T]):
    items: list[T]
    limit: int
    offset: int


class ErrorField(BaseModel):
    path: str
    issue: str


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    fields: list[ErrorField]


class ErrorEnvelope(BaseModel):
    error: ApiErrorDetail


class Done(BaseModel):
    applied: bool


class Health(BaseModel):
    status: Literal["ok"]


class CredentialsInput(Input):
    model_config = ConfigDict(str_strip_whitespace=False)


class Login(CredentialsInput):
    login_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    password: Password


class ChangePassword(CredentialsInput):
    old_password: Password
    new_password: NewPassword


class AccountCreate(Login):
    password: NewPassword
    role: Role
    student_no: Annotated[StudentNo, StringConstraints(strip_whitespace=True)] | None = None
    teacher_no: Annotated[Code, StringConstraints(strip_whitespace=True)] | None = None


class AccountState(Input):
    state: Literal["ACTIVE", "LOCKED", "DISABLED"]


class ToggleRole(Input):
    enabled: bool


class Account(BaseModel):
    account_id: int
    login_name: str
    student_no: str | None
    teacher_no: str | None
    account_state: str
    roles: list[str]


class Session(Account):
    csrf_token: str


class Department(Input):
    department_code: DepartmentCode
    department_name: Annotated[str, Field(min_length=1, max_length=100)]


class Major(Input):
    major_code: MajorCode
    major_name: Annotated[str, Field(min_length=1, max_length=100)]
    department_code: DepartmentCode
    study_years: Annotated[int, Field(ge=1, le=10)]


class AdministrativeClass(Input):
    class_no: StudentNo
    major_code: MajorCode
    entry_year: Annotated[int, Field(ge=1900, le=2200)]
    class_name: Annotated[str, Field(min_length=1, max_length=100)]


class Term(Input):
    term_code: TermCode
    starts_on: date
    week_count: Annotated[int, Field(ge=1, le=30)]
    is_current: bool = False


class Campus(Input):
    campus_code: CampusCode
    campus_name: Annotated[str, Field(min_length=1, max_length=60)]


class PeriodTime(Input):
    campus_code: CampusCode
    period_no: Period
    starts_at: time
    ends_at: time


class Teacher(Input):
    teacher_no: Code
    teacher_name: Annotated[str, Field(min_length=1, max_length=50)]
    department_code: DepartmentCode
    job_title: Annotated[str, Field(max_length=40)] | None = None
    is_active: bool = True


class Room(Input):
    room_code: RoomCode
    campus_code: CampusCode
    building: Annotated[str, Field(min_length=1, max_length=60)]
    room_name: Text40
    seat_count: Annotated[int, Field(ge=1, le=65535)]
    exam_capacity: Annotated[int, Field(ge=0, le=65535)]
    is_active: bool = True


class Course(Input):
    course_code: Code
    course_name: Annotated[str, Field(min_length=1, max_length=120)]
    department_code: DepartmentCode
    credits: Credit
    lecture_hours: Annotated[int, Field(ge=0, le=65535)] = 0
    practice_hours: Annotated[int, Field(ge=0, le=65535)] = 0
    seminar_hours: Annotated[int, Field(ge=0, le=65535)] = 0
    lab_hours: Annotated[int, Field(ge=0, le=65535)] = 0
    outside_hours: Annotated[int, Field(ge=0, le=65535)] = 0
    assessment: Literal["EXAM", "ASSESSMENT"]
    course_nature: Text40
    is_active: bool = True


class Catalog(Input):
    departments: list[Department] = Field(default_factory=list, max_length=200)
    majors: list[Major] = Field(default_factory=list, max_length=500)
    classes: list[AdministrativeClass] = Field(default_factory=list, max_length=500)
    terms: list[Term] = Field(default_factory=list, max_length=30)
    campuses: list[Campus] = Field(default_factory=list, max_length=50)
    period_times: list[PeriodTime] = Field(default_factory=list, max_length=500)
    teachers: list[Teacher] = Field(default_factory=list, max_length=500)
    rooms: list[Room] = Field(default_factory=list, max_length=500)
    courses: list[Course] = Field(default_factory=list, max_length=500)


class DepartmentView(BaseModel):
    department_code: str
    department_name: str


class MajorView(BaseModel):
    major_code: str
    major_name: str
    department_code: str
    study_years: int


class AdministrativeClassView(BaseModel):
    class_no: str
    major_code: str
    entry_year: int
    class_name: str


class TermView(BaseModel):
    term_code: str
    starts_on: date
    week_count: int
    is_current: bool


class CampusView(BaseModel):
    campus_code: str
    campus_name: str


class PeriodTimeView(BaseModel):
    campus_code: str
    period_no: int
    starts_at: time
    ends_at: time


class TeacherView(BaseModel):
    teacher_no: str
    teacher_name: str
    department_code: str
    job_title: str | None
    is_active: bool


class RoomView(BaseModel):
    room_code: str
    campus_code: str
    building: str
    room_name: str
    seat_count: int
    exam_capacity: int
    is_active: bool


class CourseView(BaseModel):
    course_code: str
    course_name: str
    department_code: str
    credits: Decimal
    lecture_hours: int
    practice_hours: int
    seminar_hours: int
    lab_hours: int
    outside_hours: int
    assessment: str
    course_nature: str
    is_active: bool


class ProgramCourse(Input):
    course_code: Code
    curriculum_group: Text40
    course_attribute: Literal["REQUIRED", "ELECTIVE", "FREE_ELECTIVE"]
    suggested_term: Annotated[int, Field(ge=1, le=30)]


class Program(Input):
    major_code: MajorCode
    entry_year: Annotated[int, Field(ge=1900, le=2200)]
    required_credits: Annotated[Decimal, Field(gt=0, le=9999.99, decimal_places=2)]
    courses: list[ProgramCourse] = Field(min_length=1, max_length=300)


class ProgramId(BaseModel):
    program_id: int


class ProgramCourseView(BaseModel):
    course_code: str
    curriculum_group: str
    course_attribute: str
    suggested_term: int


class ProgramItem(ProgramCourseView):
    course_name: str
    credits: Decimal
    passed: bool


class ProgramView(BaseModel):
    program_id: int
    major_code: str
    entry_year: int
    required_credits: Decimal
    earned_credits: Decimal
    courses: list[ProgramItem]


class AdmitStudent(Input):
    student_no: StudentNo
    student_name: Annotated[str, Field(min_length=1, max_length=50)]
    class_no: StudentNo
    admitted_on: date


class StudentImport(Input):
    batch_no: Annotated[str, Field(min_length=1, max_length=64)]
    students: list[AdmitStudent] = Field(min_length=1, max_length=1000)


class ImportResult(BaseModel):
    batch_id: int
    created: bool


class ProfileChanges(Input):
    phone: Annotated[str, Field(pattern=r"^[+0-9 ()-]{5,30}$")] | None = None
    email: Annotated[str, Field(pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$", max_length=254)] | None = None
    address: Annotated[str, Field(max_length=250)] | None = None
    name_pinyin: Annotated[str, Field(max_length=100)] | None = None


class ProfileRequest(Input):
    changes: ProfileChanges
    reason: Reason


class StatusChange(BaseModel):
    change_type: str
    effective_on: date
    old_class_no: str
    new_class_no: str
    old_state: str
    new_state: str


class Profile(BaseModel):
    student_no: str
    student_name: str
    class_no: str
    academic_state: str
    phone: str | None
    email: str | None
    address: str | None
    name_pinyin: str | None
    changes: list[StatusChange]


class RequestId(BaseModel):
    request_id: int


class RequestSummary(BaseModel):
    request_id: int
    applicant_id: int
    request_type: str
    state: RequestState
    reason: str
    requested_at: datetime


class AuditItem(BaseModel):
    actor_id: int
    action_type: str
    opinion: str | None
    acted_at: datetime


class RequestDetail(RequestSummary):
    details: dict[str, Any]
    history: list[AuditItem]


class RequestQueue(BaseModel):
    items: list[RequestSummary]
    limit: int
    after_requested_at: datetime | None
    after_request_id: int | None


class Review(Input):
    decision: Literal["APPROVE", "REJECT"]
    opinion: Annotated[str, Field(max_length=500)] | None = None


class SectionCreate(Input):
    term_code: TermCode
    class_no: StudentNo
    planned_term: Annotated[int, Field(ge=1, le=30)]
    course_code: Code
    section_no: Annotated[str, Field(min_length=1, max_length=10)]
    offering_no: Annotated[str, Field(min_length=1, max_length=32)]
    campus_code: CampusCode
    capacity: Annotated[int, Field(ge=1, le=65535)]
    teacher_no: Code


class SectionId(BaseModel):
    section_id: int


class Section(BaseModel):
    section_id: int
    offering_no: str
    section_no: str
    course_code: str
    course_name: str
    term_code: str
    campus_code: str
    offered_credits: Decimal
    capacity: int
    enrolled_count: int
    state: str


class Meeting(Input):
    room_code: RoomCode
    day_of_week: Annotated[int, Field(ge=1, le=7)]
    first_period: Period
    last_period: Period
    weeks: list[Annotated[int, Field(ge=1, le=30)]] = Field(min_length=1, max_length=30)


class MeetingId(BaseModel):
    schedule_id: int


class TimetableItem(BaseModel):
    section_id: int
    course_code: str
    day_of_week: int
    first_period: int
    last_period: int
    room_code: str


class RoundCreate(Input):
    term_code: TermCode
    round_name: Text80
    mode: Literal["DIRECT", "LOTTERY"]
    starts_at: At
    ends_at: At
    sections: list[Id] = Field(min_length=1, max_length=500)
    classes: list[StudentNo] = Field(default_factory=list, max_length=500)
    credit_limit: Annotated[Decimal, Field(gt=0, le=999.99, decimal_places=2)] | None = None


class RoundId(BaseModel):
    round_id: int


class RoundView(BaseModel):
    round_id: int
    term_code: str
    round_name: str
    mode: str
    state: str
    starts_at: datetime
    ends_at: datetime
    credit_limit: Decimal | None
    is_open: bool


class RoundSection(Section):
    enrollment_state: EnrollmentState | None


class SelectionAction(Input):
    action: Literal["SELECT", "DROP"]


class Adjustment(SelectionAction):
    student_no: StudentNo
    section_id: Id
    reason: Annotated[str, Field(min_length=1, max_length=250)]


class Enrollment(BaseModel):
    enrollment_id: int
    state: EnrollmentState


class Settlement(BaseModel):
    round_id: int
    enrolled: int
    rejected: int


class StudentSelection(BaseModel):
    section_id: int
    course_code: str
    course_name: str
    offered_credits: Decimal
    state: EnrollmentState
    study_type: str
    is_exempt: bool
    instructors: str | None


class RosterItem(BaseModel):
    enrollment_id: int
    student_no: str
    student_name: str
    class_no: str
    study_type: str


class ExamCreate(Input):
    section_id: Id
    term_code: TermCode
    period: Literal["BEGINNING", "MIDTERM", "FINAL"]
    kind: Attempt
    attempt: Annotated[int, Field(ge=1, le=255)] = 1
    starts_at: At
    ends_at: At
    rooms: list[RoomCode] = Field(min_length=1, max_length=50)
    invigilators: dict[RoomCode, Code] = Field(default_factory=dict, max_length=50)
    method: Annotated[str, Field(min_length=1, max_length=24)] = "笔试"


class ExamId(BaseModel):
    exam_id: int


class ExamSeat(BaseModel):
    exam_id: int
    exam_seat_id: int
    candidate_state: str
    room_code: str
    seat_no: int
    ticket_no: str
    course_name: str
    exam_period: str
    attempt_kind: str
    starts_at: datetime
    ends_at: datetime
    exam_method: str


class Invigilation(BaseModel):
    exam_id: int
    starts_at: datetime
    ends_at: datetime
    room_code: str
    course_name: str
    duty: str


class Deferral(Input):
    exam_seat_id: Id
    reason: Reason
    evidence_ref: Annotated[str, Field(min_length=1, max_length=250)]


class GradeRule(Input):
    gpa: Annotated[Decimal, Field(ge=0, le=4, decimal_places=2)]
    passed: bool
    min_score: Score | None = None
    max_score: Score | None = None
    level_code: Annotated[str, Field(min_length=1, max_length=12)] | None = None
    comparable_score: Score | None = None


class GradeScale(Input):
    scale_code: Code
    version_no: Annotated[int, Field(ge=1, le=65535)]
    scale_name: Annotated[str, Field(min_length=1, max_length=60)]
    value_kind: Literal["NUMERIC", "LEVEL"]
    rules: list[GradeRule] = Field(min_length=1, max_length=120)


class ScaleId(BaseModel):
    scale_id: int


class ScaleView(BaseModel):
    scale_id: int
    scale_code: str
    version_no: int
    scale_name: str
    value_kind: str


class GradeSheetCreate(Input):
    section_id: Id
    term_code: TermCode
    kind: Attempt
    opens_at: At
    closes_at: At
    exam_id: Id | None = None


class SheetId(BaseModel):
    sheet_id: int


class SheetView(BaseModel):
    sheet_id: int
    section_id: int
    exam_id: int | None
    scale_id: int | None
    state: SheetState
    term_code: str
    course_name: str
    attempt_kind: str
    opens_at: datetime
    closes_at: datetime
    submitted_at: datetime | None
    published_at: datetime | None


class SheetComponent(BaseModel):
    component_id: int
    component_name: str
    weight_percent: Decimal


class SheetEntry(BaseModel):
    enrollment_id: int
    student_no: str
    student_name: str
    grade_id: int | None = None
    candidate_state: str
    total_score: Decimal | None = None
    level_code: str | None = None
    grade_flag: str | None = None
    scores: dict[str, Decimal]


class SheetDetail(SheetView):
    components: list[SheetComponent]
    entries: list[SheetEntry]


class GradeComponents(Input):
    scale_id: Id
    weights: dict[Text40, Score] = Field(default_factory=dict, max_length=30)


class GradeEntry(Input):
    enrollment_id: Id
    scores: dict[Text40, Score] = Field(default_factory=dict, max_length=30)
    level_code: Annotated[str, Field(min_length=1, max_length=12)] | None = None
    flag: Literal["NORMAL", "ABSENT", "DEFERRED", "VIOLATION", "CHEATING", "INELIGIBLE"] = "NORMAL"


class GradeEntries(Input):
    entries: list[GradeEntry] = Field(min_length=1, max_length=500)


class WrittenGrades(BaseModel):
    grades: dict[int, int]


class SheetReview(Input):
    decision: Literal["PUBLISH", "RETURN"]
    opinion: Annotated[str, Field(max_length=500)] | None = None


class GradeView(BaseModel):
    grade_id: int
    course_code: str
    course_name: str
    course_term: str
    grade_term: str
    attempt_kind: str
    total_score: Decimal | None
    level_code: str | None
    grade_flag: str
    gpa_snapshot: Decimal | None
    credits_snapshot: Decimal
    hours_snapshot: int
    nature_snapshot: str
    passed: bool
    comparison_score: Decimal | None
    rank_no: int


class Correction(Input):
    scores: dict[Text40, Score] = Field(min_length=1, max_length=30)
    reason: Reason


class EvaluationIndicator(Input):
    name: Annotated[str, Field(min_length=1, max_length=250)]
    weight: Score
    maximum: Annotated[Decimal, Field(gt=0, le=9999.99, decimal_places=2)]


class EvaluationBatch(Input):
    term_code: TermCode
    name: Text80
    category: Annotated[str, Field(min_length=1, max_length=24)]
    starts_at: At
    ends_at: At
    indicators: list[EvaluationIndicator] = Field(min_length=1, max_length=30)


class BatchId(BaseModel):
    batch_id: int


class EvalIndicatorView(BaseModel):
    indicator_id: int
    indicator_text: str
    weight_percent: Decimal
    max_score: Decimal


class EvaluationTask(BaseModel):
    assignment_id: int
    batch_id: int
    batch_name: str
    course_name: str
    teacher_name: str
    opens_at: datetime
    closes_at: datetime
    state: str
    submitted: bool
    indicators: list[EvalIndicatorView]


class EvaluationSubmission(Input):
    scores: dict[Id, Annotated[Decimal, Field(ge=0, le=9999.99, decimal_places=2)]] = Field(min_length=1, max_length=30)
    comment: Annotated[str, Field(max_length=1000)] | None = None


class EvaluationScore(BaseModel):
    total_score: Decimal


class EvaluationResult(BaseModel):
    teacher_no: str
    batch_id: int
    section_id: int
    target_id: int
    eligible_count: int
    response_count: int
    average_score: Decimal | None
    indicator_id: int | None
    indicator_average: Decimal | None


class StudentSummary(BaseModel):
    student_no: str
    student_name: str
    class_no: str
    academic_state: str


class AdminProgram(BaseModel):
    program_id: int
    major_code: str
    entry_year: int
    required_credits: Decimal
    courses: list[ProgramCourseView]


class ExamView(BaseModel):
    exam_id: int
    section_id: int
    course_name: str
    scheduled_term_code: str
    exam_period: str
    attempt_kind: str
    attempt_no: int
    starts_at: datetime
    ends_at: datetime
    exam_method: str
    state: str


class EvaluationBatchView(BaseModel):
    batch_id: int
    term_code: str
    batch_name: str
    category: str
    opens_at: datetime
    closes_at: datetime
    state: str
