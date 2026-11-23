"""Read models for the frontend and versioned grading rules."""

from collections import defaultdict
from decimal import Decimal

from .database import log, one, permission, require, rows, transaction, value
from . import grades
from .people import CATALOG


SECTION_FIELDS = """s.section_id,s.offering_no,s.section_no,s.course_code,c.course_name,s.term_code,
                    s.campus_code,s.offered_credits,s.capacity,s.enrolled_count,s.state"""
SHEET_FIELDS = """g.sheet_id,g.section_id,g.exam_id,g.scale_id,g.state,w.term_code,c.course_name,
                  w.attempt_kind,w.opens_at,w.closes_at,g.submitted_at,g.published_at"""
SHEET_JOINS = """FROM grade_sheet g JOIN grade_input_window w ON w.window_id=g.window_id
                 JOIN section s ON s.section_id=g.section_id JOIN course c ON c.course_code=s.course_code"""


CATALOG_MODELS = {name: (table, columns) for name, table, columns in CATALOG}


def catalog_items(cn, account, kind, limit, offset):
    table, order = CATALOG_MODELS[kind]
    clause = ",".join(order)
    with cn.cursor(dictionary=True, buffered=True) as cur:
        permission(cur, account, "CATALOG")
        return rows(cur, f"SELECT * FROM `{table}` ORDER BY {clause} LIMIT %s OFFSET %s",
                    (limit, offset))


def requests_for(cn, account, limit, offset):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        return rows(cur, """SELECT * FROM request WHERE applicant_id=%s
                             ORDER BY requested_at DESC,request_id DESC LIMIT %s OFFSET %s""",
                    (account, limit, offset))


def request_queue(cn, account, state, kind, cursor_time, cursor_id, limit):
    condition = "state=%s"
    args = [state]
    if kind:
        condition += " AND request_type=%s"
        args.append(kind)
    if cursor_time is not None:
        condition += " AND (requested_at>%s OR (requested_at=%s AND request_id>%s))"
        args.extend((cursor_time, cursor_time, cursor_id))
    with cn.cursor(dictionary=True, buffered=True) as cur:
        permission(cur, account, "PROFILE_REVIEW")
        items = rows(cur, f"""SELECT * FROM request WHERE {condition}
                            ORDER BY requested_at,request_id LIMIT %s""", (*args, limit+1))
    after = items[limit-1] if len(items) > limit else None
    return {"items": items[:limit], "limit": limit,
            "after_requested_at": after["requested_at"] if after else None,
            "after_request_id": after["request_id"] if after else None}


def sections(cn, account, term, limit, offset, *, teacher=False):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "ROSTER_READ" if teacher else "SCHEDULE")
        scope = " AND EXISTS (SELECT 1 FROM section_teacher t WHERE t.section_id=s.section_id AND t.teacher_no=%s)" if teacher else ""
        args = (term, actor["teacher_no"], limit, offset) if teacher else (term, limit, offset)
        return rows(cur, f"""SELECT {SECTION_FIELDS} FROM section s JOIN course c ON c.course_code=s.course_code
                             WHERE s.term_code=%s{scope} ORDER BY s.section_id LIMIT %s OFFSET %s""", args)


def selection_rounds(cn, account, term, now):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "SELECT_COURSE")
        return rows(
            cur,
            """SELECT r.round_id,r.term_code,r.round_name,r.mode,r.state,r.starts_at,r.ends_at,r.credit_limit,
                      (r.state='OPEN' AND r.starts_at<=%s AND r.ends_at>%s) AS is_open
               FROM selection_round r JOIN student s ON s.student_no=%s
               WHERE r.term_code=%s AND r.state<>'DRAFT'
                 AND (NOT EXISTS (SELECT 1 FROM round_class rc WHERE rc.round_id=r.round_id)
                      OR EXISTS (SELECT 1 FROM round_class rc WHERE rc.round_id=r.round_id AND rc.class_no=s.class_no))
               ORDER BY r.starts_at,r.round_id""",
            (now, now, actor["student_no"], term),
        )


def round_sections(cn, account, round_id, limit, offset):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "SELECT_COURSE")
        student_no = actor["student_no"]
        allowed = value(cur, """SELECT 1 FROM selection_round r JOIN student s ON s.student_no=%s
                                 WHERE r.round_id=%s AND r.state<>'DRAFT'
                                 AND (NOT EXISTS (SELECT 1 FROM round_class rc WHERE rc.round_id=r.round_id)
                                  OR EXISTS (SELECT 1 FROM round_class rc WHERE rc.round_id=r.round_id
                                             AND rc.class_no=s.class_no))""", (student_no, round_id))
        require(allowed, "NO_ROUND")
        return rows(
            cur,
            f"""SELECT {SECTION_FIELDS},e.state AS enrollment_state FROM round_section rs
                JOIN section s ON s.section_id=rs.section_id JOIN course c ON c.course_code=s.course_code
                LEFT JOIN enrollment e ON e.section_id=s.section_id AND e.student_no=%s
                WHERE rs.round_id=%s ORDER BY s.section_id LIMIT %s OFFSET %s""",
            (student_no, round_id, limit, offset),
        )


def lottery_counts(cn, account, round_id):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        permission(cur, account, "ROUND")
        counted = one(
            cur,
            """SELECT COALESCE(SUM(action_type='WIN'),0) AS enrolled,
                      COALESCE(SUM(action_type='LOSE'),0) AS rejected
               FROM enroll_log WHERE round_id=%s AND action_type IN ('WIN','LOSE')""", (round_id,),
        )
        return {"round_id": round_id, **counted}


def teacher_timetable(cn, account, term, week):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "ROSTER_READ")
        return rows(
            cur,
            """SELECT s.section_id,s.course_code,m.day_of_week,m.first_period,m.last_period,m.room_code
               FROM schedule_teacher t JOIN schedule m ON m.schedule_id=t.schedule_id AND m.active
               JOIN section s ON s.section_id=m.section_id JOIN schedule_week w ON w.schedule_id=m.schedule_id
               WHERE t.teacher_no=%s AND s.term_code=%s AND w.week_no=%s
               ORDER BY m.day_of_week,m.first_period,m.schedule_id""", (actor["teacher_no"], term, week),
        )


def roster(cn, account, section_id):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        grades.teacher_scope(cur, account, "ROSTER_READ", section_id)
        actor = permission(cur, account, "ROSTER_READ")
        return rows(
            cur,
            """SELECT e.enrollment_id,r.student_no,r.student_name,r.class_no,r.study_type
               FROM v_teacher_roster r JOIN enrollment e ON e.section_id=r.section_id AND e.student_no=r.student_no
               WHERE r.teacher_no=%s AND r.section_id=%s ORDER BY r.student_no""",
            (actor["teacher_no"], section_id),
        )


def grade_scales(cn, account, roles):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        permission(cur, account, "GRADE_WINDOW" if "admin" in roles else "GRADE_WRITE")
        return rows(cur, """SELECT scale_id,scale_code,version_no,scale_name,value_kind FROM grade_scale
                            WHERE state='PUBLISHED' ORDER BY scale_code,version_no DESC""")


def publish_scale(cn, account, scale):
    data = scale.model_dump()
    rules = data["rules"]
    if scale.value_kind == "NUMERIC":
        require(all(r["min_score"] is not None and r["max_score"] is not None
                    and r["min_score"] <= r["max_score"] and r["level_code"] is None
                    and r["comparable_score"] is None for r in rules), "INVALID_GPA_RULE")
        ordered = sorted(rules, key=lambda r:r["min_score"])
        lower = Decimal(0)
        for rule in ordered:
            require(rule["min_score"] == lower, "INVALID_GPA_COVERAGE")
            lower = rule["max_score"] + Decimal("0.01")
        require(lower == Decimal("100.01"), "INVALID_GPA_COVERAGE")
    else:
        require(all(r["level_code"] is not None and r["comparable_score"] is not None
                    and r["min_score"] is None and r["max_score"] is None for r in rules),
                "INVALID_GPA_RULE")
        require(len({r["level_code"].casefold() for r in rules}) == len(rules), "INVALID_DUPLICATE_LEVELS")

    with transaction(cn) as cur:
        permission(cur, account, "GRADE_WINDOW")
        cur.execute("""INSERT INTO grade_scale (scale_code,version_no,scale_name,value_kind,state)
                       VALUES (%s,%s,%s,%s,'PUBLISHED')""",
                    (scale.scale_code, scale.version_no, scale.scale_name, scale.value_kind))
        scale_id = cur.lastrowid
        cur.executemany(
            """INSERT INTO gpa_rule (scale_id,min_score,max_score,level_code,comparable_score,gpa,passed)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            [(scale_id, r["min_score"], r["max_score"], r["level_code"], r["comparable_score"],
              r["gpa"], r["passed"]) for r in rules],
        )
        log(cur, account, "PUBLISH_GRADE_SCALE", "GRADE_SCALE", scale_id)
        return scale_id


def sheets(cn, account, term, state, limit, offset, *, teacher=False):
    condition = "w.term_code=%s"
    args = [term]
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "GRADE_WRITE" if teacher else "GRADE_WINDOW")
        if teacher:
            condition += " AND EXISTS (SELECT 1 FROM section_teacher t WHERE t.section_id=g.section_id AND t.teacher_no=%s)"
            args.append(actor["teacher_no"])
        if state:
            condition += " AND g.state=%s"
            args.append(state)
        return rows(cur, f"""SELECT {SHEET_FIELDS} {SHEET_JOINS} WHERE {condition}
                             ORDER BY g.sheet_id DESC LIMIT %s OFFSET %s""", (*args, limit, offset))


def sheet_detail(cn, account, sheet_id, *, teacher=False):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        permission(cur, account, "GRADE_WRITE" if teacher else "GRADE_WINDOW")
        sheet = one(cur, f"SELECT {SHEET_FIELDS} {SHEET_JOINS} WHERE g.sheet_id=%s", (sheet_id,))
        require(sheet, "NO_GRADE_SHEET")
        if teacher:
            grades.teacher_scope(cur, account, "GRADE_WRITE", sheet["section_id"])
        sheet["components"] = rows(cur, """SELECT component_id,component_name,weight_percent FROM grade_component
                                           WHERE sheet_id=%s ORDER BY order_no""", (sheet_id,))
        expected = grades.grade_roster(cur, sheet)
        grade_items = defaultdict(dict)
        for item in rows(
            cur,
            """SELECT g.grade_id,c.component_name,i.item_score FROM grade g JOIN grade_item i ON i.grade_id=g.grade_id
               JOIN grade_component c ON c.component_id=i.component_id WHERE g.sheet_id=%s""",
            (sheet_id,),
        ):
            grade_items[item["grade_id"]][item["component_name"]] = item["item_score"]
        sheet["entries"] = []
        for item in rows(
            cur,
            """SELECT e.enrollment_id,e.student_no,s.student_name,g.grade_id,g.total_score,g.level_code,g.grade_flag
               FROM enrollment e JOIN student s ON s.student_no=e.student_no
               LEFT JOIN grade g ON g.sheet_id=%s AND g.enrollment_id=e.enrollment_id
               WHERE e.section_id=%s ORDER BY e.student_no""",
            (sheet_id, sheet["section_id"]),
        ):
            if item["enrollment_id"] in expected:
                item["candidate_state"] = expected[item["enrollment_id"]]
                item["scores"] = grade_items.get(item["grade_id"], {})
                sheet["entries"].append(item)
        return sheet


def evaluation_tasks(cn, account, term):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "EVAL_SUBMIT")
        tasks = rows(
            cur,
            """SELECT a.assignment_id,b.batch_id,b.batch_name,b.scheme_id,b.opens_at,b.closes_at,b.state,
                      c.course_name,teach.teacher_name,(result.assignment_id IS NOT NULL) AS submitted
               FROM eval_assignment a JOIN eval_target t ON t.target_id=a.target_id
               JOIN eval_batch b ON b.batch_id=t.batch_id JOIN teacher teach ON teach.teacher_no=t.teacher_no
               JOIN section s ON s.section_id=t.section_id JOIN course c ON c.course_code=s.course_code
               JOIN enrollment e ON e.enrollment_id=a.enrollment_id
               LEFT JOIN evaluation result ON result.assignment_id=a.assignment_id
               WHERE b.term_code=%s AND e.student_no=%s ORDER BY b.opens_at,a.assignment_id""",
            (term, actor["student_no"]),
        )
        if tasks:
            schemes = tuple({task["scheme_id"] for task in tasks})
            marks = ",".join(["%s"] * len(schemes))
            indices = defaultdict(list)
            for indicator in rows(
                cur,
                f"""SELECT scheme_id,indicator_id,indicator_text,weight_percent,max_score FROM eval_indicator
                    WHERE scheme_id IN ({marks}) ORDER BY order_no,indicator_id""", schemes,
            ):
                indices[indicator.pop("scheme_id")].append(indicator)
            for task in tasks:
                task["indicators"] = indices[task.pop("scheme_id")]
        return tasks


def evaluation_results(cn, account, batch_id):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        permission(cur, account, "EVAL_ADMIN")
        return rows(cur, """SELECT * FROM v_teacher_eval_result WHERE batch_id=%s
                            ORDER BY teacher_no,section_id,indicator_id""", (batch_id,))
