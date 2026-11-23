"""Grade-sheet transactions and per-student published-grade reads."""

import json
from decimal import Decimal, ROUND_HALF_UP

from academic import approve
from database import log, one, permission, require, rows, transaction, value


SPECIAL_FLAGS = {"ABSENT", "DEFERRED", "VIOLATION", "CHEATING", "INELIGIBLE"}


def open_grade_sheet(cn, account, section_id, term, kind, opens, closes, exam_id=None):
    require(opens < closes and kind in {"REGULAR", "MAKEUP", "DEFERRED"}, "INVALID_GRADE_WINDOW")
    with transaction(cn) as cur:
        permission(cur, account, "GRADE_WINDOW")
        require(one(cur, "SELECT term_code FROM term WHERE term_code=%s FOR UPDATE", (term,)),
                "NO_TERM")
        section = one(cur, "SELECT * FROM section WHERE section_id=%s FOR UPDATE", (section_id,))
        require(section, "NO_SECTION")
        attempt = 1
        if exam_id is not None:
            exam = one(cur, "SELECT * FROM exam WHERE exam_id=%s", (exam_id,))
            require(exam and exam["section_id"] == section_id and exam["state"] == "PUBLISHED"
                    and exam["scheduled_term_code"] == term and exam["attempt_kind"] == kind
                    and exam["ends_at"] <= opens, "GRADE_EXAM_MISMATCH")
            attempt = exam["attempt_no"]
        else:
            require(section["offered_assessment"] == "ASSESSMENT"
                    and section["term_code"] == term and kind == "REGULAR", "GRADE_EXAM_REQUIRED")

        window = one(cur, """SELECT * FROM grade_input_window
                             WHERE term_code=%s AND attempt_kind=%s FOR UPDATE""", (term, kind))
        if window:
            require((window["opens_at"], window["closes_at"]) == (opens, closes), "GRADE_WINDOW_EXISTS")
            window_id = window["window_id"]
        else:
            cur.execute(
                """INSERT INTO grade_input_window (term_code,attempt_kind,opens_at,closes_at)
                   VALUES (%s,%s,%s,%s)""", (term, kind, opens, closes),
            )
            window_id = cur.lastrowid
        cur.execute(
            """INSERT INTO grade_sheet (section_id,window_id,exam_id,attempt_no)
               VALUES (%s,%s,%s,%s)""", (section_id, window_id, exam_id, attempt),
        )
        sheet_id = cur.lastrowid
        log(cur, account, "OPEN_GRADE_SHEET", "GRADE_SHEET", sheet_id)
        return sheet_id


def sheet_for_update(cur, sheet_id):
    sheet = one(
        cur,
        """SELECT s.*,w.opens_at,w.closes_at,gs.value_kind
           FROM grade_sheet s JOIN grade_input_window w ON w.window_id=s.window_id
           LEFT JOIN grade_scale gs ON gs.scale_id=s.scale_id
           WHERE s.sheet_id=%s FOR UPDATE OF s""", (sheet_id,),
    )
    require(sheet, "NO_GRADE_SHEET")
    return sheet


def teacher_scope(cur, account, action, section_id):
    teacher_no = permission(cur, account, action)["teacher_no"]
    require(value(cur, """SELECT 1 FROM section_teacher WHERE teacher_no=%s AND section_id=%s""",
                  (teacher_no, section_id)), "FORBIDDEN")


def editable(cur, account, sheet_id, now):
    sheet = sheet_for_update(cur, sheet_id)
    teacher_scope(cur, account, "GRADE_WRITE", sheet["section_id"])
    require(sheet["state"] in {"EMPTY", "DRAFT"}, "GRADE_SHEET_LOCKED")
    if now >= sheet["closes_at"] and sheet["state"] == "DRAFT":
        returned = value(cur, """SELECT 1 FROM grade_sheet_log WHERE sheet_id=%s
                                 AND action_type='RETURN' AND acted_at<=%s LIMIT 1""", (sheet_id, now))
    else:
        returned = False
    require(sheet["opens_at"] <= now and (now < sheet["closes_at"] or returned),
            "GRADE_WINDOW_CLOSED")
    return sheet


def configure_grades(cn, account, sheet_id, scale_id, weights, now):
    with transaction(cn) as cur:
        editable(cur, account, sheet_id, now)
        require(not value(cur, "SELECT 1 FROM grade WHERE sheet_id=%s LIMIT 1", (sheet_id,)),
                "GRADE_COMPONENTS_LOCKED")
        kind = value(cur, """SELECT value_kind FROM grade_scale
                             WHERE scale_id=%s AND state='PUBLISHED'""", (scale_id,))
        require(kind is not None, "GRADE_SCALE_UNAVAILABLE")
        if kind == "NUMERIC":
            require(weights and sum(weights.values()) == 100
                    and all(0 < weight <= 100 for weight in weights.values()), "GRADE_WEIGHTS")
        else:
            require(not weights, "LEVEL_HAS_NO_WEIGHTED_ITEMS")
        cur.execute("DELETE FROM grade_component WHERE sheet_id=%s", (sheet_id,))
        if weights:
            cur.executemany(
                """INSERT INTO grade_component (sheet_id,component_name,weight_percent,order_no)
                   VALUES (%s,%s,%s,%s)""",
                [(sheet_id, key, value, index) for index, (key, value) in enumerate(weights.items(), 1)],
            )
        cur.execute("UPDATE grade_sheet SET scale_id=%s,state='DRAFT' WHERE sheet_id=%s",
                    (scale_id, sheet_id))
        log(cur, account, "CONFIGURE_GRADES", "GRADE_SHEET", sheet_id)


def grade_roster(cur, sheet):
    if sheet["exam_id"] is not None:
        return {r["enrollment_id"]: r["candidate_state"] for r in rows(
            cur,
            """SELECT s.enrollment_id,s.candidate_state FROM exam_seat s
               JOIN enrollment e ON e.enrollment_id=s.enrollment_id
               WHERE s.exam_id=%s AND e.section_id=%s""",
            (sheet["exam_id"], sheet["section_id"]))}
    return {r["enrollment_id"]: "SCHEDULED" for r in rows(
        cur, """SELECT enrollment_id FROM enrollment WHERE section_id=%s
                AND state IN ('ENROLLED','COMPLETED')""", (sheet["section_id"],))}


def components(cur, sheet_id):
    return {r["component_name"]: (r["component_id"], r["weight_percent"]) for r in rows(
        cur, "SELECT * FROM grade_component WHERE sheet_id=%s ORDER BY order_no", (sheet_id,))}


def calculate(cur, sheet, rubric, scores, level_code, flag):
    require(sheet["scale_id"] is not None, "GRADE_SCALE_REQUIRED")
    if flag in SPECIAL_FLAGS:
        require(not scores and level_code is None, "SPECIAL_FLAG_WITH_SCORE")
        return None, None
    require(flag == "NORMAL", "INVALID_GRADE_FLAG")
    if sheet["value_kind"] == "LEVEL":
        require(not scores and isinstance(level_code, str) and level_code, "LEVEL_REQUIRED")
        rules = rows(cur, """SELECT rule_id,gpa FROM gpa_rule WHERE scale_id=%s AND level_code=%s""",
                     (sheet["scale_id"], level_code))
        total = None
    else:
        require(level_code is None and scores.keys() == rubric.keys(), "GRADE_WRONG_ITEMS")
        require(all(0 <= Decimal(score) <= 100 for score in scores.values()), "GRADE_SCORE_RANGE")
        total = sum(Decimal(scores[key]) * weight / 100 for key, (_, weight) in rubric.items())
        total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        rules = rows(cur, """SELECT rule_id,gpa FROM gpa_rule WHERE scale_id=%s
                             AND %s BETWEEN min_score AND max_score""", (sheet["scale_id"], total))
    require(len(rules) == 1, "GRADE_RULE_MISSING_OR_OVERLAP")
    return total, rules[0]


def write_grade(cur, sheet, rubric, enrollment_id, scores, level_code, flag, *, version=None):
    total, rule = calculate(cur, sheet, rubric, scores, level_code, flag)
    score_values = (total, level_code, flag, rule["rule_id"] if rule else None,
                    rule["gpa"] if rule else None)
    existing = one(cur, "SELECT grade_id,version_no FROM grade WHERE sheet_id=%s AND enrollment_id=%s",
                   (sheet["sheet_id"], enrollment_id))
    if existing:
        grade_id = existing["grade_id"]
        next_version = version if version is not None else existing["version_no"]
        cur.execute(
            """UPDATE grade SET total_score=%s,level_code=%s,grade_flag=%s,rule_id=%s,gpa_snapshot=%s,
                                version_no=%s WHERE grade_id=%s""",
            (*score_values, next_version, grade_id),
        )
        cur.execute("DELETE FROM grade_item WHERE grade_id=%s", (grade_id,))
    else:
        cur.execute(
            """INSERT INTO grade
               (sheet_id,enrollment_id,total_score,level_code,grade_flag,rule_id,gpa_snapshot,
                credits_snapshot,hours_snapshot,nature_snapshot)
               SELECT %s,%s,%s,%s,%s,%s,%s,offered_credits,offered_hours,offered_nature
               FROM section WHERE section_id=%s""",
            (sheet["sheet_id"], enrollment_id, *score_values, sheet["section_id"]),
        )
        grade_id = cur.lastrowid
    if scores:
        cur.executemany("INSERT INTO grade_item VALUES (%s,%s,%s)",
                        [(grade_id, rubric[key][0], score) for key, score in scores.items()])
    return grade_id


def save_grades(cn, account, sheet_id, entries, now):
    """Save the complete supplied batch or roll back every row on an invalid entry."""
    with transaction(cn) as cur:
        sheet = editable(cur, account, sheet_id, now)
        roster = grade_roster(cur, sheet)
        rubric = components(cur, sheet_id)
        saved = {}
        for enrollment_id, scores, level, flag in entries:
            require(enrollment_id in roster, "GRADE_STUDENT_NOT_IN_ROSTER")
            require((roster[enrollment_id] == "DEFERRED") == (flag == "DEFERRED"),
                    "GRADE_DEFERRAL_MISMATCH")
            saved[enrollment_id] = write_grade(cur, sheet, rubric, enrollment_id, scores, level, flag)
        log(cur, account, "SAVE_GRADES", "GRADE_SHEET", sheet_id)
        return saved


def sheet_log(cur, sheet_id, account, action, now, opinion=None):
    cur.execute(
        """INSERT INTO grade_sheet_log (sheet_id,actor_id,action_type,opinion,acted_at)
           VALUES (%s,%s,%s,%s,%s)""", (sheet_id, account, action, opinion, now),
    )


def submit_grades(cn, account, sheet_id, now):
    with transaction(cn) as cur:
        # Use the same section lock as add/drop so the assessment roster stays stable at submission.
        one(cur, """SELECT s.section_id FROM grade_sheet g JOIN section s ON s.section_id=g.section_id
                    WHERE g.sheet_id=%s AND g.exam_id IS NULL FOR SHARE OF s""", (sheet_id,))
        sheet = editable(cur, account, sheet_id, now)
        roster = grade_roster(cur, sheet)
        registered = rows(cur, """SELECT grade_id,enrollment_id,total_score,level_code,grade_flag,rule_id
                                  FROM grade WHERE sheet_id=%s""", (sheet_id,))
        obsolete = []
        if sheet["exam_id"] is None:
            obsolete = [(r["grade_id"],) for r in registered if r["enrollment_id"] not in roster]
            registered = [r for r in registered if r["enrollment_id"] in roster]
        require(roster and {r["enrollment_id"] for r in registered} == roster.keys(),
                "GRADE_INCOMPLETE")
        for grade in registered:
            require(grade["grade_flag"] != "NORMAL" or
                    (grade["rule_id"] is not None and
                     (grade["total_score"] is not None or grade["level_code"] is not None)),
                    "GRADE_INCOMPLETE")
            require((roster[grade["enrollment_id"]] == "DEFERRED") ==
                    (grade["grade_flag"] == "DEFERRED"), "GRADE_DEFERRAL_MISMATCH")
        if obsolete:
            cur.executemany("DELETE FROM grade_item WHERE grade_id=%s", obsolete)
            cur.executemany("DELETE FROM grade WHERE grade_id=%s", obsolete)
        cur.execute("UPDATE grade_sheet SET state='SUBMITTED',submitted_at=%s WHERE sheet_id=%s",
                    (now, sheet_id))
        sheet_log(cur, sheet_id, account, "SUBMIT", now)


def review_sheet(cn, account, sheet_id, approve_sheet, now, opinion=None):
    with transaction(cn) as cur:
        permission(cur, account, "GRADE_PUBLISH")
        sheet = sheet_for_update(cur, sheet_id)
        require(sheet["state"] == "SUBMITTED" and now >= sheet["submitted_at"], "GRADE_NOT_SUBMITTED")
        if approve_sheet:
            cur.execute("UPDATE grade_sheet SET state='PUBLISHED',published_at=%s WHERE sheet_id=%s",
                        (now, sheet_id))
        else:
            cur.execute("UPDATE grade_sheet SET state='DRAFT',submitted_at=NULL WHERE sheet_id=%s",
                        (sheet_id,))
        sheet_log(cur, sheet_id, account, "PUBLISH" if approve_sheet else "RETURN", now, opinion)


def grade_snapshot(cur, grade):
    items = rows(cur, """SELECT c.component_name,i.item_score FROM grade_item i
                         JOIN grade_component c ON c.component_id=i.component_id
                         WHERE i.grade_id=%s ORDER BY c.order_no""", (grade["grade_id"],))
    return {
        "version": grade["version_no"],
        "flag": grade["grade_flag"],
        "total": str(grade["total_score"]) if grade["total_score"] is not None else None,
        "level": grade["level_code"],
        "gpa": str(grade["gpa_snapshot"]) if grade["gpa_snapshot"] is not None else None,
        "items": {r["component_name"]: str(r["item_score"]) for r in items},
    }


def request_correction(cn, account, grade_id, scores, reason):
    with transaction(cn) as cur:
        sheet_id = value(cur, "SELECT sheet_id FROM grade WHERE grade_id=%s", (grade_id,))
        sheet = sheet_for_update(cur, sheet_id)
        teacher_scope(cur, account, "CORRECTION_SUBMIT", sheet["section_id"])
        require(sheet["state"] == "PUBLISHED", "GRADE_NOT_PUBLISHED")
        grade = one(cur, "SELECT * FROM grade WHERE grade_id=%s FOR UPDATE", (grade_id,))
        require(grade["grade_flag"] == "NORMAL" and sheet["value_kind"] == "NUMERIC",
                "CORRECTION_FIXTURE_NUMERIC_ONLY")
        snapshot = grade_snapshot(cur, grade)
        total, rule = calculate(cur, sheet, components(cur, sheet_id), scores, None, "NORMAL")
        proposed = {"version": grade["version_no"] + 1, "flag": "NORMAL", "total": str(total),
                    "level": None, "gpa": str(rule["gpa"]),
                    "items": {key: str(score) for key, score in scores.items()}}
        cur.execute(
            """INSERT INTO request (applicant_id,request_type,reason)
               VALUES (%s,'GRADE_CORRECTION',%s)""", (account, reason),
        )
        request_id = cur.lastrowid
        cur.execute(
            """INSERT INTO correction_req (request_id,grade_id,expected_version,old_snapshot,proposed_snapshot)
               VALUES (%s,%s,%s,%s,%s)""",
            (request_id, grade_id, grade["version_no"], json.dumps(snapshot, ensure_ascii=False),
             json.dumps(proposed, ensure_ascii=False)),
        )
        return request_id


def approve_correction(cn, account, request_id):
    with transaction(cn) as cur:
        permission(cur, account, "CORRECTION_REVIEW")
        req = one(
            cur,
            """SELECT r.state,r.applicant_id,c.*,g.sheet_id FROM request r
               JOIN correction_req c ON c.request_id=r.request_id JOIN grade g ON g.grade_id=c.grade_id
               WHERE r.request_id=%s AND r.request_type='GRADE_CORRECTION' FOR UPDATE OF r""",
            (request_id,),
        )
        require(req, "NO_REQUEST")
        if req["state"] == "APPROVED":
            return False
        require(req["state"] in {"PENDING", "IN_REVIEW"}, "REQUEST_FINAL")
        require(req["applicant_id"] != account, "CORRECTION_SELF_APPROVAL")
        sheet = sheet_for_update(cur, req["sheet_id"])
        require(sheet["state"] == "PUBLISHED", "GRADE_NOT_PUBLISHED")
        grade = one(cur, "SELECT * FROM grade WHERE grade_id=%s FOR UPDATE", (req["grade_id"],))
        require(grade["version_no"] == req["expected_version"], "STALE_GRADE_VERSION")
        scores = json.loads(req["proposed_snapshot"])["items"]
        new_version = grade["version_no"] + 1
        write_grade(cur, sheet, components(cur, sheet["sheet_id"]), grade["enrollment_id"],
                    scores, None, "NORMAL", version=new_version)
        after = one(cur, "SELECT * FROM grade WHERE grade_id=%s", (grade["grade_id"],))
        approve(cur, account, request_id)
        cur.execute(
            """INSERT INTO grade_log
               (grade_id,correction_request_id,actor_id,old_version,new_version,old_snapshot,new_snapshot)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (grade["grade_id"], request_id, account, grade["version_no"], new_version,
             req["old_snapshot"], json.dumps(grade_snapshot(cur, after), ensure_ascii=False)),
        )
        return True


def published_grades(cn, account, *, best=False):
    order = """v.passed DESC,v.comparison_score DESC,v.gpa_snapshot DESC,
               sh.published_at DESC,v.grade_id DESC"""
    with cn.cursor(dictionary=True, buffered=True) as cur:
        student_no = permission(cur, account, "GRADE_READ")["student_no"]
        return rows(
            cur,
            f"""WITH ranked AS (
                SELECT v.*,ROW_NUMBER() OVER (PARTITION BY v.course_code ORDER BY {order}) AS rank_no
                FROM v_student_grade v JOIN grade g ON g.grade_id=v.grade_id
                JOIN grade_sheet sh ON sh.sheet_id=g.sheet_id WHERE v.student_no=%s
               )
               SELECT * FROM ranked WHERE NOT %s OR rank_no=1 ORDER BY course_code,rank_no""",
            (student_no, best),
        )


def program_credits(cn, account):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        student_no = permission(cur, account, "GRADE_READ")["student_no"]
        plan = one(cur, """SELECT p.program_id,p.required_credits FROM student s
                          JOIN admin_class c ON c.class_no=s.class_no
                          JOIN program p ON p.major_code=c.major_code AND p.entry_year=c.entry_year
                          WHERE s.student_no=%s""", (student_no,))
        require(plan, "NO_PROGRAM")
        course_codes = {r["course_code"] for r in rows(
            cur, "SELECT course_code FROM program_course WHERE program_id=%s", (plan["program_id"],))}
    earned = sum((row["credits_snapshot"] for row in published_grades(cn, account, best=True)
                  if row["passed"] and row["course_code"] in course_codes), Decimal(0))
    return earned, plan["required_credits"]
