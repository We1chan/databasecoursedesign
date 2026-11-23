"""Catalog writes, student profiles and the supported approval workflows."""

from decimal import Decimal
import json

from cryptography.fernet import Fernet, InvalidToken

from .academic import approve
from .database import log, one, permission, require, rows, transaction, value
from .errors import Fault
from . import exams, grades


CATALOG = (
    ("departments", "department", ("department_code",)),
    ("majors", "major", ("major_code",)),
    ("classes", "admin_class", ("class_no",)),
    ("terms", "term", ("term_code",)),
    ("campuses", "campus", ("campus_code",)),
    ("period_times", "period_time", ("campus_code", "period_no")),
    ("teachers", "teacher", ("teacher_no",)),
    ("rooms", "classroom", ("room_code",)),
    ("courses", "course", ("course_code",)),
)
PROFILE_FIELDS = {"PHONE":"phone_cipher", "EMAIL":"email", "ADDRESS":"address",
                  "NAME_PINYIN":"name_pinyin"}
REQUEST_PERMISSIONS = {"INFO_CHANGE":"PROFILE_REVIEW", "DEFERRAL":"DEFERRAL_REVIEW",
                       "GRADE_CORRECTION":"CORRECTION_REVIEW"}


def update_account_state(cn, administrator, account, state):
    with transaction(cn) as cur:
        permission(cur, administrator, "ACCOUNT_ADMIN")
        require(account != administrator or state == "ACTIVE", "SELF_ACCOUNT_LOCK")
        target = one(cur, "SELECT account_state FROM user_account WHERE account_id=%s FOR UPDATE", (account,))
        require(target, "NO_ACCOUNT")
        if state == target["account_state"]:
            return False
        cur.execute("UPDATE user_account SET account_state=%s WHERE account_id=%s", (state, account))
        cur.execute("DELETE FROM auth_session WHERE account_id=%s", (account,))
        log(cur, administrator, "SET_ACCOUNT_STATE", "USER_ACCOUNT", account)
        return True


def update_catalog(cn, account, payload, now):
    batches = payload.model_dump()
    require(any(batches.values()), "INVALID_EMPTY_CATALOG")
    current = [term for term in batches["terms"] if term["is_current"]]
    require(len(current) <= 1, "INVALID_MULTIPLE_CURRENT_TERMS")
    with transaction(cn) as cur:
        permission(cur, account, "CATALOG")
        for key, table, identity in CATALOG:
            records = batches[key]
            if not records:
                continue
            if key == "terms":
                save_terms(cur, payload.terms)
                continue
            if key in {"teachers", "rooms"}:
                records = [{**record, "source_system":"API", "synced_at":now} for record in records]
            if key == "period_times":
                validate_periods(cur, records)
            if key == "rooms":
                validate_rooms(cur, records)
            columns = tuple(records[0])
            parameters = ",".join(["%s"] * len(columns))
            changes = ",".join(f"`{name}`=incoming.`{name}`" for name in columns if name not in identity)
            fields = ",".join(f"`{name}`" for name in columns)
            cur.executemany(
                f"INSERT INTO `{table}` ({fields}) VALUES ({parameters}) AS incoming "
                f"ON DUPLICATE KEY UPDATE {changes}",
                [tuple(record[col] for col in columns) for record in records],
            )
        log(cur, account, "UPDATE_CATALOG", "CATALOG", "batch")


def save_terms(cur, terms):
    for term in terms:
        old = one(cur, "SELECT starts_on,week_count FROM term WHERE term_code=%s FOR UPDATE",
                  (term.term_code,))
        if old and (old["starts_on"] != term.starts_on or old["week_count"] > term.week_count):
            last_week = value(
                cur,
                """SELECT MAX(w.week_no) FROM section s JOIN schedule m ON m.section_id=s.section_id
                   JOIN schedule_week w ON w.schedule_id=m.schedule_id
                   WHERE s.term_code=%s AND m.active""",
                (term.term_code,),
            )
            require(last_week is None or (old["starts_on"] == term.starts_on
                                          and last_week <= term.week_count), "TERM_IN_USE")
        cur.execute(
            """INSERT INTO term (term_code,starts_on,week_count) VALUES (%s,%s,%s) AS incoming
               ON DUPLICATE KEY UPDATE starts_on=incoming.starts_on,week_count=incoming.week_count""",
            (term.term_code, term.starts_on, term.week_count),
        )

    for term in terms:
        if "is_current" not in term.model_fields_set:
            continue
        if term.is_current:
            cur.execute("UPDATE term SET is_current=FALSE WHERE current_guard=1 AND term_code<>%s",
                        (term.term_code,))
        cur.execute("UPDATE term SET is_current=%s WHERE term_code=%s", (term.is_current, term.term_code))


def validate_rooms(cur, records):
    for record in records:
        old = one(cur, """SELECT campus_code,seat_count,is_active FROM classroom
                          WHERE room_code=%s FOR UPDATE""", (record["room_code"],))
        if not old:
            continue
        moved = old["campus_code"] != record["campus_code"]
        deactivated = old["is_active"] and not record["is_active"]
        if moved or deactivated or record["seat_count"] < old["seat_count"]:
            capacity = value(cur, """SELECT MAX(s.capacity) FROM schedule m
                                     JOIN section s ON s.section_id=m.section_id
                                     WHERE m.room_code=%s AND m.active""", (record["room_code"],))
            require(capacity is None or (not moved and not deactivated
                                         and record["seat_count"] >= capacity), "ROOM_IN_USE")


def validate_periods(cur, records):
    for record in records:
        start, end = record["starts_at"], record["ends_at"]
        require(start.tzinfo is None and end.tzinfo is None and end > start, "INVALID_PERIOD_TIME")
        old = one(cur, """SELECT CAST(starts_at AS CHAR) AS starts,CAST(ends_at AS CHAR) AS ends
                          FROM period_time WHERE campus_code=%s AND period_no=%s FOR UPDATE""",
                  (record["campus_code"], record["period_no"]))
        if old and (old["starts"], old["ends"]) != (start.isoformat(), end.isoformat()):
            in_use = value(cur, """SELECT 1 FROM schedule m JOIN classroom r ON r.room_code=m.room_code
                                   WHERE r.campus_code=%s AND m.active
                                    AND m.first_period<=%s AND m.last_period>=%s LIMIT 1""",
                           (record["campus_code"], record["period_no"], record["period_no"]))
            require(not in_use, "PERIOD_IN_USE")


def save_program(cn, account, program):
    data = program.model_dump()
    entries = data["courses"]
    require(len({e["course_code"].casefold() for e in entries}) == len(entries), "INVALID_DUPLICATE_COURSES")
    with transaction(cn) as cur:
        permission(cur, account, "CATALOG")
        cur.execute(
            """INSERT INTO program (major_code,entry_year,required_credits) VALUES (%s,%s,%s) AS incoming
               ON DUPLICATE KEY UPDATE program_id=LAST_INSERT_ID(program_id),
               required_credits=incoming.required_credits""",
            (data["major_code"], data["entry_year"], data["required_credits"]),
        )
        program_id = cur.lastrowid
        cur.execute("DELETE FROM program_course WHERE program_id=%s", (program_id,))
        cur.executemany(
            """INSERT INTO program_course VALUES (%s,%s,%s,%s,%s)""",
            [(program_id, e["course_code"], e["curriculum_group"],
              e["course_attribute"], e["suggested_term"]) for e in entries],
        )
        log(cur, account, "REPLACE_PLAN_COURSES", "PROGRAM", program_id)
        return program_id


def student_program(cn, account):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "GRADE_READ")
        plan = one(cur, """SELECT p.program_id,p.major_code,p.entry_year,p.required_credits
                           FROM student s JOIN admin_class a ON a.class_no=s.class_no
                           JOIN program p ON p.major_code=a.major_code AND p.entry_year=a.entry_year
                           WHERE s.student_no=%s""", (actor["student_no"],))
        require(plan, "NO_PROGRAM")
        courses = rows(
            cur,
            """SELECT c.course_code,c.course_name,c.credits,i.curriculum_group,
                      i.course_attribute,i.suggested_term
               FROM program_course i JOIN course c ON c.course_code=i.course_code
               WHERE i.program_id=%s ORDER BY i.suggested_term,c.course_code""", (plan["program_id"],),
        )
    passed = {g["course_code"]:g["credits_snapshot"]
              for g in grades.published_grades(cn, account, best=True) if g["passed"]}
    for course in courses:
        course["passed"] = course["course_code"] in passed
    plan["courses"] = courses
    plan["earned_credits"] = sum((passed.get(c["course_code"], 0) for c in courses), Decimal(0))
    return plan


def cipher_for(key):
    try:
        return Fernet(key.encode("ascii"))
    except ValueError as exc:
        raise Fault(503, "DATA_KEY_REQUIRED", "资料加密配置不可用") from exc


def reveal(cipher, stored):
    if stored is None:
        return None
    try:
        return cipher.decrypt(bytes(stored)).decode("utf8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise Fault(503, "PROFILE_DATA_UNAVAILABLE", "无法读取加密资料") from exc


def seal(cipher, text):
    return None if text is None else cipher.encrypt(text.encode("utf8"))


def profile(cn, account, key):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        actor = permission(cur, account, "SELF_PROFILE")
        card = one(cur, """SELECT student_no,student_name,class_no,academic_state,phone_cipher,
                                  email,address,name_pinyin FROM student WHERE student_no=%s""",
                   (actor["student_no"],))
        require(card, "NO_STUDENT")
        stored_phone = card.pop("phone_cipher")
        card["phone"] = None if stored_phone is None else reveal(cipher_for(key), stored_phone)
        card["changes"] = rows(cur, """SELECT change_type,effective_on,old_class_no,new_class_no,
                                              old_state,new_state FROM status_change
                                       WHERE student_no=%s ORDER BY effective_on DESC,change_id DESC""",
                               (actor["student_no"],))
        return card


def submit_profile(cn, account, changes, reason, key, now):
    require(changes, "INVALID_EMPTY_CHANGES")
    cipher = cipher_for(key)
    with transaction(cn) as cur:
        actor = permission(cur, account, "SELF_PROFILE")
        student = one(cur, """SELECT phone_cipher,email,address,name_pinyin FROM student
                              WHERE student_no=%s FOR UPDATE""", (actor["student_no"],))
        require(student, "NO_STUDENT")
        cur.execute("""INSERT INTO request (applicant_id,request_type,reason,requested_at)
                       VALUES (%s,'INFO_CHANGE',%s,%s)""", (account, reason, now))
        request_id = cur.lastrowid
        records = []
        for name, new in changes.items():
            code = name.upper()
            old = student[PROFILE_FIELDS[code]]
            if code == "PHONE":
                require(reveal(cipher, old) != new, "INVALID_UNCHANGED_FIELD")
            else:
                require(old != new, "INVALID_UNCHANGED_FIELD")
                old = seal(cipher, old)
            records.append((request_id, code, old, seal(cipher, new)))
        cur.executemany("INSERT INTO info_change_item VALUES (%s,%s,%s,%s)", records)
        return request_id


def request_row(cur, request_id, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = one(cur, "SELECT * FROM request WHERE request_id=%s" + suffix, (request_id,))
    require(row, "NO_REQUEST")
    return row


def reviewable_request_types(cur, account):
    granted = {record["permission_code"] for record in rows(
        cur,
        """SELECT DISTINCT rp.permission_code FROM user_account a
           JOIN user_role ur ON ur.account_id=a.account_id
           JOIN role_permission rp ON rp.role_code=ur.role_code
           WHERE a.account_id=%s AND a.account_state='ACTIVE'""",
        (account,),
    )}
    kinds = [kind for kind, code in REQUEST_PERMISSIONS.items() if code in granted]
    require(kinds, "FORBIDDEN")
    return kinds


def reviewable_request(cur, account, request_id):
    kinds = reviewable_request_types(cur, account)
    request = request_row(cur, request_id)
    require(request["request_type"] in kinds, "FORBIDDEN")
    return request


def apply_profile(cur, request, key):
    cipher = cipher_for(key)
    student_no = value(cur, "SELECT student_no FROM user_account WHERE account_id=%s",
                       (request["applicant_id"],))
    student = one(cur, """SELECT phone_cipher,email,address,name_pinyin FROM student
                          WHERE student_no=%s FOR UPDATE""", (student_no,))
    require(student, "NO_STUDENT")
    values = rows(cur, "SELECT * FROM info_change_item WHERE request_id=%s", (request["request_id"],))
    require(values, "INVALID_EMPTY_CHANGES")
    for item in values:
        code = item["field_code"]
        column = PROFILE_FIELDS[code]
        old, new = item["old_value_cipher"], item["new_value_cipher"]
        if code != "PHONE":
            old, new = reveal(cipher, old), reveal(cipher, new)
        require(student[column] == old, "STALE_PROFILE")
        cur.execute(f"UPDATE student SET `{column}`=%s WHERE student_no=%s", (new, student_no))


def request_detail(cn, account, request_id, key):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        request = reviewable_request(cur, account, request_id)
        kind = request["request_type"]
        if kind == "INFO_CHANGE":
            cipher = cipher_for(key)
            items = rows(cur, """SELECT field_code,old_value_cipher,new_value_cipher FROM info_change_item
                                 WHERE request_id=%s ORDER BY field_code""", (request_id,))
            request["details"] = {"changes": [
                {"field":item["field_code"].lower(), "old_value":reveal(cipher,item["old_value_cipher"]),
                 "new_value":reveal(cipher,item["new_value_cipher"])} for item in items
            ]}
        elif kind == "DEFERRAL":
            request["details"] = one(cur, """SELECT exam_seat_id,evidence_ref FROM deferral_req
                                             WHERE request_id=%s""", (request_id,)) or {}
        elif kind == "GRADE_CORRECTION":
            details = one(cur, """SELECT grade_id,expected_version,old_snapshot,proposed_snapshot
                                  FROM correction_req WHERE request_id=%s""", (request_id,))
            request["details"] = {**details, "old_snapshot":json.loads(details["old_snapshot"]),
                                  "proposed_snapshot":json.loads(details["proposed_snapshot"])} if details else {}
        request["history"] = rows(cur, """SELECT actor_id,action_type,opinion,acted_at FROM approval_log
                                         WHERE request_id=%s ORDER BY approval_id""", (request_id,))
        return request


def decide_request(cn, account, request_id, decision, opinion, key, now):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        kind = reviewable_request(cur, account, request_id)["request_type"]
    if decision == "APPROVE" and kind == "DEFERRAL":
        return exams.approve_deferral(cn, account, request_id, now, opinion)
    if decision == "APPROVE" and kind == "GRADE_CORRECTION":
        return grades.approve_correction(cn, account, request_id, opinion)

    with transaction(cn) as cur:
        permission(cur, account, REQUEST_PERMISSIONS[kind])
        request = request_row(cur, request_id, lock=True)
        final = "APPROVED" if decision == "APPROVE" else "REJECTED"
        if request["state"] == final:
            return False
        require(request["state"] in {"PENDING", "IN_REVIEW"}, "REQUEST_FINAL")
        if decision == "APPROVE":
            apply_profile(cur, request, key)
            approve(cur, account, request_id, opinion)
        else:
            cur.execute("UPDATE request SET state='REJECTED' WHERE request_id=%s", (request_id,))
            cur.execute("""INSERT INTO approval_log (request_id,actor_id,action_type,opinion)
                           VALUES (%s,%s,'REJECT',%s)""", (request_id, account, opinion))
        return True
