"""Admission, course planning, scheduling and enrollment transactions."""

from hashlib import sha256
import json

from mysql.connector import IntegrityError

from .database import Rejected, log, one, permission, require, rows, transaction, value


def import_students(cn, account, batch_no, students):
    """Return the batch and whether this input was newly imported."""
    student_nos = {entry[0] for entry in students}
    require(students and len(student_nos) == len(students), "DUPLICATE_IMPORT_STUDENT")
    require(all(len(no) == 10 and no.isascii() and no.isdigit() for no in student_nos),
            "INVALID_STUDENT_NO")
    digest = sha256(json.dumps(sorted(students), ensure_ascii=False, default=str).encode()).digest()
    try:
        with transaction(cn) as cur:
            permission(cur, account, "ADMISSION")
            batch_id = claim_import_batch(cur, batch_no, digest, "PENDING")
            batch = one(cur, "SELECT * FROM import_batch WHERE batch_id=%s FOR UPDATE", (batch_id,))
            require(batch["content_sha256"] == digest, "IMPORT_CONTENT_CHANGED")
            if batch["state"] == "IMPORTED":
                return batch_id, False
            cur.executemany(
                """INSERT INTO student (student_no,student_name,class_no,admitted_on,batch_id)
                   VALUES (%s,%s,%s,%s,%s)""",
                [(*entry, batch_id) for entry in students],
            )
            cur.execute("UPDATE import_batch SET state='IMPORTED' WHERE batch_id=%s", (batch_id,))
            log(cur, account, "IMPORT_STUDENTS", "IMPORT_BATCH", batch_id)
            return batch_id, True
    except IntegrityError as error:
        with transaction(cn) as cur:
            batch_id = claim_import_batch(cur, batch_no, digest, "FAILED")
            actual = value(cur, "SELECT content_sha256 FROM import_batch WHERE batch_id=%s", (batch_id,))
            if actual == digest:
                log(cur, account, "IMPORT_STUDENTS", "IMPORT_BATCH", batch_id, f"SQL_{error.errno}")
        raise


def claim_import_batch(cur, batch_no, digest, state):
    cur.execute(
        """INSERT INTO import_batch (source_system,source_batch_no,content_sha256,state)
           VALUES ('ADMISSIONS',%s,%s,%s)
           ON DUPLICATE KEY UPDATE batch_id=LAST_INSERT_ID(batch_id)""",
        (batch_no, digest, state),
    )
    return cur.lastrowid


def student_profile(cn, account):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        student_no = permission(cur, account, "SELF_PROFILE")["student_no"]
        card = one(cur, """SELECT student_no,student_name,class_no,academic_state,phone_cipher
                           FROM student WHERE student_no=%s""", (student_no,))
        require(card, "STUDENT_REQUIRED")
        card["changes"] = rows(cur, """SELECT change_type,effective_on,old_class_no,new_class_no,
                                              old_state,new_state FROM status_change
                                       WHERE student_no=%s ORDER BY effective_on,change_id""",
                               (student_no,))
        return card


def request_phone_change(cn, account, sealed_phone):
    """Use an opaque ciphertext payload supplied by the caller."""
    with transaction(cn) as cur:
        actor = permission(cur, account, "SELF_PROFILE")
        student = one(
            cur, "SELECT phone_cipher FROM student WHERE student_no=%s FOR UPDATE",
            (actor["student_no"],),
        )
        require(student is not None, "STUDENT_REQUIRED")
        cur.execute(
            """INSERT INTO request (applicant_id,request_type,reason)
               VALUES (%s,'INFO_CHANGE','联系方式更新')""",
            (account,),
        )
        request_id = cur.lastrowid
        cur.execute(
            """INSERT INTO info_change_item (request_id,field_code,old_value_cipher,new_value_cipher)
               VALUES (%s,'PHONE',%s,%s)""",
            (request_id, student["phone_cipher"], sealed_phone),
        )
        return request_id


def apply_phone_change(cn, account, request_id):
    with transaction(cn) as cur:
        permission(cur, account, "PROFILE_REVIEW")
        change = one(
            cur,
            """SELECT r.state,u.student_no,i.old_value_cipher,i.new_value_cipher
               FROM request r JOIN user_account u ON u.account_id=r.applicant_id
               JOIN info_change_item i ON i.request_id=r.request_id AND i.field_code='PHONE'
               WHERE r.request_id=%s AND r.request_type='INFO_CHANGE' FOR UPDATE OF r""",
            (request_id,),
        )
        require(change is not None, "NO_REQUEST")
        if change["state"] == "APPROVED":
            return False
        require(change["state"] in {"PENDING", "IN_REVIEW"}, "REQUEST_FINAL")
        student = one(cur, "SELECT phone_cipher FROM student WHERE student_no=%s FOR UPDATE",
                      (change["student_no"],))
        require(student["phone_cipher"] == change["old_value_cipher"], "STALE_PROFILE")
        cur.execute("UPDATE student SET phone_cipher=%s WHERE student_no=%s",
                    (change["new_value_cipher"], change["student_no"]))
        approve(cur, account, request_id)
        return True


def approve(cur, account, request_id, opinion=None):
    cur.execute("UPDATE request SET state='APPROVED' WHERE request_id=%s", (request_id,))
    cur.execute("""INSERT INTO approval_log (request_id,actor_id,action_type,opinion)
                   VALUES (%s,%s,'APPROVE',%s)""", (request_id, account, opinion))


def add_term(cn, account, term_code, starts_on, week_count, *, is_current=False):
    with transaction(cn) as cur:
        permission(cur, account, "CATALOG")
        if is_current:
            cur.execute("UPDATE term SET is_current=FALSE WHERE current_guard=1")
        cur.execute(
            """INSERT INTO term (term_code,starts_on,week_count,is_current)
               VALUES (%s,%s,%s,%s)""", (term_code, starts_on, week_count, is_current),
        )
        log(cur, account, "ADD_TERM", "TERM", term_code)


def add_course(cn, account, code, name, department, credits, hours, assessment, nature):
    with transaction(cn) as cur:
        permission(cur, account, "CATALOG")
        cur.execute(
            """INSERT INTO course
               (course_code,course_name,department_code,credits,lecture_hours,assessment,course_nature)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (code, name, department, credits, hours, assessment, nature),
        )
        log(cur, account, "ADD_COURSE", "COURSE", code)


def replace_plan_courses(cn, account, program_id, entries):
    with transaction(cn) as cur:
        permission(cur, account, "CATALOG")
        require(one(cur, "SELECT program_id FROM program WHERE program_id=%s FOR UPDATE", (program_id,)),
                "NO_PROGRAM")
        cur.execute("DELETE FROM program_course WHERE program_id=%s", (program_id,))
        cur.executemany(
            """INSERT INTO program_course
               (program_id,course_code,curriculum_group,course_attribute,suggested_term)
               VALUES (%s,%s,%s,%s,%s)""", [(program_id, *entry) for entry in entries],
        )
        log(cur, account, "REPLACE_PLAN_COURSES", "PROGRAM", program_id)


def create_offering(cn, account, term, class_no, planned_term, course, section_no,
                    offering_no, campus, capacity, instructor):
    with transaction(cn) as cur:
        permission(cur, account, "SCHEDULE")
        require(one(cur, "SELECT term_code FROM term WHERE term_code=%s FOR UPDATE", (term,)),
                "NO_TERM")
        course_record = one(
            cur,
            """SELECT c.credits,c.course_nature,c.assessment,
                      c.lecture_hours+c.practice_hours+c.seminar_hours+c.lab_hours+c.outside_hours AS hours
               FROM admin_class a JOIN program p ON p.major_code=a.major_code AND p.entry_year=a.entry_year
               JOIN program_course pc ON pc.program_id=p.program_id AND pc.suggested_term=%s
               JOIN course c ON c.course_code=pc.course_code AND c.is_active
               WHERE a.class_no=%s AND c.course_code=%s""",
            (planned_term, class_no, course),
        )
        require(course_record, "COURSE_NOT_IN_PLANNED_TERM")
        require(value(cur, "SELECT 1 FROM teacher WHERE teacher_no=%s AND is_active", (instructor,)),
                "NO_INSTRUCTOR")
        cur.execute(
            """INSERT INTO section
               (offering_no,course_code,term_code,section_no,campus_code,
                offered_credits,offered_hours,offered_nature,offered_assessment,capacity)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (offering_no, course, term, section_no, campus, course_record["credits"],
             course_record["hours"], course_record["course_nature"], course_record["assessment"], capacity),
        )
        section = cur.lastrowid
        cur.execute("INSERT INTO section_class VALUES (%s,%s)", (section, class_no))
        cur.execute("INSERT INTO section_teacher VALUES (%s,%s,'LEAD')", (section, instructor))
        log(cur, account, "CREATE_OFFERING", "SECTION", section)
        return section


def meeting(cur, section, schedule_term, room, day, periods, weeks):
    first, last = periods
    require(1 <= day <= 7 and 1 <= first <= last <= 14, "INVALID_SLOT")
    require(weeks and all(1 <= week <= schedule_term["week_count"] for week in weeks),
            "INVALID_WEEK")
    room_slot = one(
        cur,
        """SELECT c.seat_count,p.starts_at,q.ends_at
           FROM classroom c
           JOIN period_time p ON p.campus_code=c.campus_code AND p.period_no=%s
           JOIN period_time q ON q.campus_code=c.campus_code AND q.period_no=%s
           WHERE c.room_code=%s AND c.is_active AND c.campus_code=%s""",
        (first, last, room, section["campus_code"]),
    )
    require(room_slot is not None, "ROOM_OR_PERIOD_UNAVAILABLE")
    require(room_slot["seat_count"] >= section["capacity"], "CLASSROOM_CAPACITY")
    team = rows(cur, """SELECT st.teacher_no FROM section_teacher st
                        JOIN teacher t ON t.teacher_no=st.teacher_no AND t.is_active
                        WHERE st.section_id=%s""", (section["section_id"],))
    require(team, "NO_INSTRUCTOR")
    return room_slot, team


def schedule_section(cn, account, section_id, room, day, periods, weeks):
    with transaction(cn) as cur:
        permission(cur, account, "SCHEDULE")
        term = value(cur, "SELECT term_code FROM section WHERE section_id=%s", (section_id,))
        require(term, "NO_SECTION")
        schedule_term = one(cur, "SELECT * FROM term WHERE term_code=%s FOR UPDATE", (term,))
        section = one(cur, "SELECT * FROM section WHERE section_id=%s FOR UPDATE", (section_id,))
        require(section["state"] in {"DRAFT", "SCHEDULED"}, "SECTION_NOT_SCHEDULABLE")
        room_slot, team = meeting(cur, section, schedule_term, room, day, periods, weeks)
        week_params = ",".join(["%s"] * len(weeks))
        clashes = rows(
            cur,
            f"""SELECT DISTINCT old.schedule_id, old.section_id, old.room_code,
                      EXISTS (
                          SELECT 1 FROM schedule_teacher a JOIN section_teacher b
                            ON b.teacher_no=a.teacher_no
                          WHERE a.schedule_id=old.schedule_id AND b.section_id=%s
                      ) AS teacher_clash,
                      EXISTS (
                          SELECT 1 FROM section_class a JOIN section_class b ON b.class_no=a.class_no
                          WHERE a.section_id=old.section_id AND b.section_id=%s
                      ) AS class_clash
               FROM schedule old
               JOIN section s ON s.section_id=old.section_id
               JOIN schedule_week w ON w.schedule_id=old.schedule_id
               JOIN classroom r ON r.room_code=old.room_code
               JOIN period_time a ON a.campus_code=r.campus_code AND a.period_no=old.first_period
               JOIN period_time b ON b.campus_code=r.campus_code AND b.period_no=old.last_period
               WHERE old.active AND s.term_code=%s AND old.day_of_week=%s
                 AND w.week_no IN ({week_params}) AND a.starts_at < %s AND b.ends_at > %s
               ORDER BY old.schedule_id""",
            (section_id, section_id, section["term_code"], day, *sorted(weeks),
             room_slot["ends_at"], room_slot["starts_at"]),
        )
        for clash in clashes:
            require(clash["room_code"] != room, "ROOM_CONFLICT")
            require(not clash["teacher_clash"], "TEACHER_CONFLICT")
            require(not clash["class_clash"], "CLASS_CONFLICT")
            require(clash["section_id"] != section_id, "SECTION_TIME_CONFLICT")
        cur.execute("""INSERT INTO schedule (section_id,room_code,day_of_week,first_period,last_period)
                       VALUES (%s,%s,%s,%s,%s)""", (section_id, room, day, *periods))
        schedule_id = cur.lastrowid
        cur.executemany("INSERT INTO schedule_week VALUES (%s,%s)",
                        [(schedule_id, week) for week in sorted(weeks)])
        cur.executemany("INSERT INTO schedule_teacher VALUES (%s,%s)",
                        [(schedule_id, member["teacher_no"]) for member in team])
        cur.execute("UPDATE section SET state='SCHEDULED' WHERE section_id=%s", (section_id,))
        log(cur, account, "SCHEDULE_SECTION", "SCHEDULE", schedule_id)
        return schedule_id


def open_round(cn, account, term, name, mode, starts, ends, sections, credit_limit=None, classes=(), *, seed=None):
    require(sections and len(set(sections)) == len(sections), "INVALID_ROUND_SECTIONS")
    require(mode in {"DIRECT", "LOTTERY"} and ends > starts, "INVALID_ROUND")
    with transaction(cn) as cur:
        permission(cur, account, "ROUND")
        require(one(cur, "SELECT term_code FROM term WHERE term_code=%s FOR UPDATE", (term,)),
                "NO_TERM")
        busy = value(cur, """SELECT COUNT(*) FROM selection_round WHERE term_code=%s AND
                             (state='SETTLING' OR (state='OPEN' AND starts_at < %s AND ends_at > %s))""",
                     (term, ends, starts))
        require(not busy, "ROUND_OVERLAP")
        for section_id in sorted(sections):
            section = one(cur, "SELECT state,term_code FROM section WHERE section_id=%s FOR UPDATE",
                          (section_id,))
            require(section and section["term_code"] == term, "ROUND_WRONG_TERM")
            require(section["state"] in {"SCHEDULED", "OPEN"}, "SECTION_NOT_SCHEDULED")
        cur.execute("""INSERT INTO selection_round
                       (term_code,round_name,mode,starts_at,ends_at,credit_limit,state,lottery_seed)
                       VALUES (%s,%s,%s,%s,%s,%s,'OPEN',%s)""",
                    (term, name, mode, starts, ends, credit_limit, seed))
        round_id = cur.lastrowid
        cur.executemany("INSERT INTO round_section VALUES (%s,%s)",
                        [(round_id, section) for section in sorted(sections)])
        if classes:
            cur.executemany("INSERT INTO round_class VALUES (%s,%s)",
                            [(round_id, class_no) for class_no in classes])
        cur.executemany("UPDATE section SET state='OPEN' WHERE section_id=%s",
                        [(section,) for section in sorted(sections)])
        log(cur, account, "OPEN_ROUND", "SELECTION_ROUND", round_id)
        return round_id


def round_lock(cur, round_id, exclusive=False):
    term = value(cur, "SELECT term_code FROM selection_round WHERE round_id=%s", (round_id,))
    require(term is not None, "NO_ROUND")
    cur.execute("SELECT term_code FROM term WHERE term_code=%s FOR SHARE", (term,))
    lock = "FOR UPDATE" if exclusive else "FOR SHARE"
    return one(cur, f"SELECT * FROM selection_round WHERE round_id=%s {lock}", (round_id,))


def selection_context(cur, student_no, section_id, selection_round):
    student = one(cur, "SELECT * FROM student WHERE student_no=%s FOR UPDATE", (student_no,))
    require(student and student["academic_state"] == "ACTIVE", "STUDENT_NOT_ACTIVE")
    section = one(cur, "SELECT * FROM section WHERE section_id=%s FOR UPDATE", (section_id,))
    require(section and section["term_code"] == selection_round["term_code"]
            and section["state"] == "OPEN", "SECTION_NOT_OPEN")
    round_id = selection_round["round_id"]
    require(value(cur, "SELECT 1 FROM round_section WHERE round_id=%s AND section_id=%s",
                  (round_id, section_id)), "SECTION_OUTSIDE_ROUND")
    class_visible = value(
        cur,
        """SELECT NOT EXISTS (SELECT 1 FROM round_class WHERE round_id=%s)
                  OR EXISTS (SELECT 1 FROM round_class WHERE round_id=%s AND class_no=%s)""",
        (round_id, round_id, student["class_no"]),
    )
    require(class_visible, "CLASS_OUTSIDE_ROUND")
    enrollment = one(cur, """SELECT * FROM enrollment
                            WHERE student_no=%s AND section_id=%s FOR UPDATE""",
                     (student_no, section_id))
    return section, enrollment


def reserve_seat(cur, student_no, section, selection_round):
    section_id = section["section_id"]
    active = rows(
        cur,
        """SELECT s.course_code,s.offered_credits
           FROM enrollment e JOIN section s ON s.section_id=e.section_id
           WHERE e.student_no=%s AND s.term_code=%s AND e.state IN ('ENROLLED','COMPLETED')""",
        (student_no, section["term_code"]),
    )
    require(all(other["course_code"] != section["course_code"] for other in active),
            "DUPLICATE_COURSE")
    limit = selection_round["credit_limit"]
    total = section["offered_credits"] + sum(other["offered_credits"] for other in active)
    require(limit is None or total <= limit, "CREDIT_LIMIT")
    conflict = value(
        cur,
        """SELECT 1 FROM schedule target
           JOIN schedule_week tw ON tw.schedule_id=target.schedule_id
           JOIN classroom tr ON tr.room_code=target.room_code
           JOIN period_time ts ON ts.campus_code=tr.campus_code AND ts.period_no=target.first_period
           JOIN period_time tf ON tf.campus_code=tr.campus_code AND tf.period_no=target.last_period
           JOIN schedule_week ew ON ew.week_no=tw.week_no
           JOIN schedule enrolled ON enrolled.schedule_id=ew.schedule_id AND enrolled.active
            AND enrolled.day_of_week=target.day_of_week
           JOIN classroom er ON er.room_code=enrolled.room_code
           JOIN period_time es ON es.campus_code=er.campus_code AND es.period_no=enrolled.first_period
           JOIN period_time ef ON ef.campus_code=er.campus_code AND ef.period_no=enrolled.last_period
           JOIN section s ON s.section_id=enrolled.section_id AND s.term_code=%s
           JOIN enrollment e ON e.section_id=enrolled.section_id AND e.student_no=%s
            AND e.state IN ('ENROLLED','COMPLETED')
           WHERE target.section_id=%s AND target.active AND ts.starts_at < ef.ends_at
            AND tf.ends_at > es.starts_at LIMIT 1""",
        (section["term_code"], student_no, section_id),
    )
    require(not conflict, "TIMETABLE_CONFLICT")
    cur.execute("""UPDATE section SET enrolled_count=enrolled_count+1
                   WHERE section_id=%s AND enrolled_count < capacity""", (section_id,))
    require(cur.rowcount == 1, "FULL")


def write_enrollment(cur, account, round_id, student_no, section_id,
                     previous, state, action, now, reason=None):
    if previous is None:
        cur.execute("""INSERT INTO enrollment (student_no,section_id,round_id,state,selected_at)
                       VALUES (%s,%s,%s,%s,%s)""", (student_no, section_id, round_id, state, now))
        enrollment_id = cur.lastrowid
    else:
        enrollment_id = previous["enrollment_id"]
        selected_at = previous["selected_at"]
        if action == "SELECT" or (action == "ADJUST" and state == "ENROLLED"):
            selected_at = now
        cur.execute("""UPDATE enrollment SET round_id=%s,state=%s,selected_at=%s
                       WHERE enrollment_id=%s""", (round_id, state, selected_at, enrollment_id))
    cur.execute(
        """INSERT INTO enroll_log
           (enrollment_id,round_id,actor_id,old_state,new_state,action_type,reason,acted_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (enrollment_id, round_id, account, previous["state"] if previous else None,
         state, action, reason, now),
    )
    return enrollment_id, state


def release_seat(cur, section_id):
    cur.execute("""UPDATE section SET enrolled_count=enrolled_count-1
                   WHERE section_id=%s AND enrolled_count>0""", (section_id,))
    require(cur.rowcount == 1, "COUNT_MISMATCH")


def change_selection(cn, account, round_id, section_id, now, *, drop=False):
    with transaction(cn) as cur:
        actor = permission(cur, account, "SELECT_COURSE")
        student_no = actor["student_no"]
        selection_round = round_lock(cur, round_id)
        require(selection_round["state"] == "OPEN"
                and selection_round["starts_at"] <= now < selection_round["ends_at"], "ROUND_CLOSED")
        section, previous = selection_context(cur, student_no, section_id, selection_round)
        old = previous["state"] if previous else None
        if drop:
            if old == "DROPPED":
                return previous["enrollment_id"], old
            require(old in {"PENDING", "ENROLLED"}, "NOT_DROPPABLE")
            if old == "PENDING":
                require(previous["round_id"] == round_id, "WRONG_PENDING_ROUND")
            if old == "ENROLLED":
                release_seat(cur, section_id)
            state, action = "DROPPED", "DROP"
        else:
            if old == "ENROLLED" or (old == "PENDING" and previous["round_id"] == round_id):
                return previous["enrollment_id"], old
            require(old not in {"COMPLETED", "PENDING"}, "SELECTION_ALREADY_EXISTS")
            if selection_round["mode"] == "DIRECT":
                reserve_seat(cur, student_no, section, selection_round)
                state = "ENROLLED"
            else:
                state = "PENDING"
            action = "SELECT"
        return write_enrollment(cur, account, round_id, student_no, section_id,
                                previous, state, action, now)


def adjust_selection(cn, account, round_id, student_no, section_id, now, *, drop, reason):
    require(reason and len(reason) <= 250, "ADJUST_REASON_REQUIRED")
    with transaction(cn) as cur:
        permission(cur, account, "ROUND")
        selection_round = round_lock(cur, round_id)
        require(selection_round["state"] in {"OPEN", "CLOSED"}, "ROUND_UNAVAILABLE")
        section, previous = selection_context(cur, student_no, section_id, selection_round)
        old = previous["state"] if previous else None
        if drop:
            require(old == "ENROLLED", "NOT_DROPPABLE")
            release_seat(cur, section_id)
            state = "DROPPED"
        else:
            require(old in {None, "DROPPED", "REJECTED"}, "SELECTION_ALREADY_EXISTS")
            reserve_seat(cur, student_no, section, selection_round)
            state = "ENROLLED"
        return write_enrollment(cur, account, round_id, student_no, section_id,
                                previous, state, "ADJUST", now, reason)


def close_lottery(cn, account, round_id, now, seed=None):
    """Settle a round in one transaction using the stored draw order."""
    with transaction(cn) as cur:
        permission(cur, account, "ROUND")
        selection_round = round_lock(cur, round_id, exclusive=True)
        require(selection_round["mode"] == "LOTTERY", "NOT_LOTTERY")
        if selection_round["state"] == "CLOSED":
            return []
        require(selection_round["state"] == "OPEN" and now >= selection_round["ends_at"],
                "LOTTERY_STILL_OPEN")
        seed = selection_round["lottery_seed"] or seed
        require(seed is not None and len(seed) == 32, "INVALID_SEED")
        cur.execute("""UPDATE selection_round SET state='SETTLING',lottery_seed=%s
                       WHERE round_id=%s""", (seed, round_id))
        pending = rows(
            cur,
            """SELECT enrollment_id,student_no,section_id FROM enrollment
               WHERE round_id=%s AND state='PENDING'
               ORDER BY SHA2(CONCAT(HEX(%s),':',enrollment_id),256),enrollment_id""",
            (round_id, seed),
        )
        outcomes = []
        for applicant in pending:
            student_no, section_id = applicant["student_no"], applicant["section_id"]
            previous = one(cur, "SELECT * FROM enrollment WHERE enrollment_id=%s",
                           (applicant["enrollment_id"],))
            try:
                section, previous = selection_context(cur, student_no, section_id, selection_round)
                reserve_seat(cur, student_no, section, selection_round)
            except Rejected as rejection:
                reason = str(rejection)
                outcome = write_enrollment(cur, account, round_id, student_no, section_id,
                                           previous, "REJECTED", "LOSE", now, reason)
            else:
                outcome = write_enrollment(cur, account, round_id, student_no, section_id,
                                           previous, "ENROLLED", "WIN", now)
            outcomes.append(outcome)
        cur.execute("UPDATE selection_round SET state='CLOSED' WHERE round_id=%s", (round_id,))
        log(cur, account, "CLOSE_LOTTERY", "SELECTION_ROUND", round_id)
        return outcomes


def student_timetable(cn, account, term, week):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        student_no = permission(cur, account, "SELECT_COURSE")["student_no"]
        return rows(
            cur,
            """SELECT s.section_id,s.course_code,m.day_of_week,m.first_period,m.last_period,m.room_code
               FROM enrollment e JOIN section s ON s.section_id=e.section_id
               JOIN schedule m ON m.section_id=s.section_id AND m.active
               JOIN schedule_week w ON w.schedule_id=m.schedule_id AND w.week_no=%s
               WHERE e.student_no=%s AND e.state IN ('ENROLLED','COMPLETED') AND s.term_code=%s
               ORDER BY m.day_of_week,m.first_period,m.schedule_id""",
            (week, student_no, term),
        )


def student_selections(cn, account, term):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        student_no = permission(cur, account, "SELECT_COURSE")["student_no"]
        return rows(
            cur,
            """SELECT s.section_id,c.course_code,c.course_name,s.offered_credits,
                      e.state,e.study_type,e.is_exempt,
                      (SELECT GROUP_CONCAT(t.teacher_name ORDER BY t.teacher_no SEPARATOR '、')
                       FROM section_teacher st JOIN teacher t ON t.teacher_no=st.teacher_no
                       WHERE st.section_id=s.section_id) AS instructors
               FROM enrollment e JOIN section s ON s.section_id=e.section_id
               JOIN course c ON c.course_code=s.course_code
               WHERE e.student_no=%s AND s.term_code=%s AND e.state IN ('ENROLLED','COMPLETED')
               ORDER BY c.course_code,s.section_id""",
            (student_no, term),
        )


def teacher_roster(cn, account, section):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        teacher_no = permission(cur, account, "ROSTER_READ")["teacher_no"]
        return rows(
            cur,
            """SELECT student_no,student_name,class_no,study_type FROM v_teacher_roster
               WHERE teacher_no=%s AND section_id=%s ORDER BY student_no""",
            (teacher_no, section),
        )
