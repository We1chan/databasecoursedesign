"""Stable error envelopes shared by the server and its OpenAPI contract."""

from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from mysql.connector import Error as DatabaseError
from starlette.exceptions import HTTPException as StarletteError

from .database import Rejected
from .models import ErrorEnvelope


class Fault(Exception):
    def __init__(self, status, code, message, headers=None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


MESSAGES = {
    "FULL": "教学班已满",
    "DUPLICATE_COURSE": "已选同一课程的其他教学班",
    "TIMETABLE_CONFLICT": "与已选课程的时间冲突",
    "CREDIT_LIMIT": "超过选课学分上限",
    "ROUND_CLOSED": "当前轮次不在选课时间",
    "ROUND_OVERLAP": "本学期选课轮次时间重叠",
    "STALE_GRADE_VERSION": "成绩已变更，请刷新后重新提交申请",
    "STALE_PROFILE": "资料已变更，请刷新后重新提交申请",
    "GRADE_INCOMPLETE": "尚有成绩未录入",
    "GRADE_SHEET_LOCKED": "当前登记单不可编辑",
    "GRADE_WINDOW_CLOSED": "当前不在成绩录入时间",
    "ROOM_CONFLICT": "教室在该时段已占用",
    "TEACHER_CONFLICT": "授课教师在该时段已占用",
    "CLASS_CONFLICT": "行政班在该时段已排课",
    "EXAM_ROOM_CONFLICT": "考场在该时段已占用",
    "EXAM_STUDENT_CONFLICT": "考生在该时段已有考试",
    "INVIGILATOR_CONFLICT": "监考教师在该时段已有安排",
    "FORBIDDEN": "没有访问该资源的权限",
    "INVALID_CREDENTIALS": "账号或密码错误，或账号不可用",
    "REQUEST_FINAL": "申请已办结",
    "EVAL_ALREADY_SUBMITTED": "已提交评价",
}
INVALID = {
    "CLASSROOM_CAPACITY", "EXAM_CAPACITY", "GRADE_WEIGHTS", "GRADE_WRONG_ITEMS",
    "GRADE_SCORE_RANGE", "EVAL_WEIGHTS", "EVAL_WRONG_INDICATORS", "EVAL_SCORE_RANGE",
    "GRADE_DEFERRAL_MISMATCH", "GRADE_STUDENT_NOT_IN_ROSTER", "LEVEL_REQUIRED",
    "SPECIAL_FLAG_WITH_SCORE", "CORRECTION_NUMERIC_ONLY", "WRONG_EXAM_TERM",
}
DB_FAULTS = {
    1062: (409, "DUPLICATE_RECORD", "编号或关联记录已存在"),
    1451: (409, "REFERENCE_IN_USE", "记录已被其他数据引用"),
    1452: (422, "REFERENCE_INVALID", "引用的记录不存在"),
    3819: (422, "CONSTRAINT_VIOLATION", "值不符合数据库约束"),
    1264: (422, "CONSTRAINT_VIOLATION", "值超出范围"),
    1406: (422, "CONSTRAINT_VIOLATION", "输入内容过长"),
    1205: (503, "TRANSACTION_RETRY", "操作繁忙，请刷新后重试"),
    1213: (503, "TRANSACTION_RETRY", "操作繁忙，请刷新后重试"),
    1040: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
    1045: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
    1049: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
    2002: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
    2003: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
    2006: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
    2013: (503, "DATABASE_UNAVAILABLE", "数据库暂不可用"),
}
ERROR_RESPONSES = {
    status: {"model": ErrorEnvelope, "description": description}
    for status, description in {
        400:"请求参数不匹配", 401:"未登录或凭据无效", 403:"操作未授权",
        404:"资源不存在", 409:"业务状态冲突", 415:"请求媒体类型不支持",
        422:"字段或约束校验失败", 429:"登录频率受限", 500:"服务器无法完成操作",
        503:"数据库暂不可用或事务需要重试",
    }.items()
}


def error(status, code, message, fields=(), headers=None):
    return JSONResponse(status_code=status, headers=headers or {},
                        content={"error": {"code": code, "message": message, "fields": list(fields)}})


def install_handlers(app):
    @app.exception_handler(Fault)
    async def handle_fault(_request, exc):
        return error(exc.status, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(Rejected)
    async def handle_rejected(_request, exc):
        code = str(exc)
        status = 409
        if code == "INVALID_CREDENTIALS":
            status = 401
        elif code == "FORBIDDEN":
            status = 403
        elif code.startswith("NO_"):
            status = 404
        elif code.startswith("INVALID_") or code in INVALID:
            status = 422
        return error(status, code, MESSAGES.get(code, "当前条件不允许该操作"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request, exc):
        fields = [{"path": ".".join(map(str, item["loc"])), "issue": item["type"]}
                  for item in exc.errors()]
        return error(422, "VALIDATION_ERROR", "请求字段校验失败", fields)

    @app.exception_handler(DatabaseError)
    async def handle_database(_request, exc):
        return error(*DB_FAULTS.get(exc.errno, (500, "DATABASE_ERROR", "数据库操作失败")))

    @app.exception_handler(ResponseValidationError)
    async def handle_response(_request, _exc):
        return error(500, "RESPONSE_ERROR", "服务器无法返回业务数据")

    @app.exception_handler(StarletteError)
    async def handle_http(_request, exc):
        if exc.status_code == 404:
            return error(404, "NOT_FOUND", "资源不存在", headers=exc.headers)
        return error(exc.status_code, "HTTP_ERROR", "请求无法处理", headers=exc.headers)
