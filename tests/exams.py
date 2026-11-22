"""Exam scheduling and deferral transactions exercised by the integration suite."""

from academic import approve
from database import log, one, permission, require, rows, transaction, value


def exam_students(cur, section_id, kind, scheduled_term, starts_at):
    base = """SELECT e.enrollment_id,e.student_no
              FROM enrollment e
              WHERE e.section_id=%s AND e.state IN ('ENROLLED','COMPLETED')"""
    if kind == "REGULAR":
        return rows(cur, base + " ORDER BY e.student_no", (section_id,))
    if kind == "DEFERRED":
        eligibility = """ AND EXISTS (
             SELECT 1 FROM exam_seat es JOIN exam ex ON ex.exam_id=es.exam_id
             WHERE es.enrollment_id=e.enrollment_id AND es.candidate_state='DEFERRED'
              AND ex.attempt_kind='REGULAR' AND ex.state='PUBLISHED'
              AND ex.scheduled_term_code<>%s AND ex.ends_at<=%s
        )"""
    else:
        eligibility = """ AND EXISTS (
             SELECT 1 FROM grade g JOIN grade_sheet sh ON sh.sheet_id=g.sheet_id
             JOIN grade_input_window w ON w.window_id=sh.window_id
             LEFT JOIN gpa_rule r ON r.rule_id=g.rule_id
             WHERE g.enrollment_id=e.enrollment_id AND sh.state='PUBLISHED'
              AND w.attempt_kind='REGULAR' AND w.term_code<>%s AND sh.published_at<=%s
              AND ((g.grade_flag='NORMAL' AND r.passed=0) OR g.grade_flag='ABSENT')
        )"""
    return rows(cur, base + eligibility + " ORDER BY e.student_no",
                (section_id, scheduled_term, starts_at))


def publish_exam(cn, account, section_id, term, period, kind, starts, ends, rooms,
                 *, invigilators=None, attempt=1):
    require(starts < ends and kind in {"REGULAR", "MAKEUP", "DEFERRED"}, "INVALID_EXAM")
    require(rooms and len(rooms) == len(set(rooms)), "INVALID_EXAM_ROOMS")
    invigilators = invigilators or {}
    require(invigilators.keys() <= set(rooms)
            and len(set(invigilators.values())) == len(invigilators), "INVALID_INVIGILATORS")

    with transaction(cn) as cur:
        permission(cur, account, "EXAM")
        # The course fixture serializes publishing across terms to handle absolute-time overlaps.
        terms = rows(cur, "SELECT term_code FROM term ORDER BY term_code FOR UPDATE")
        require(term in {row["term_code"] for row in terms}, "NO_EXAM_TERM")
        section = one(cur, "SELECT * FROM section WHERE section_id=%s FOR UPDATE", (section_id,))
        require(section and section["offered_assessment"] == "EXAM", "NO_EXAM_REQUIRED")
        require(
            (kind == "REGULAR" and section["term_code"] == term)
            or (kind != "REGULAR" and section["term_code"] != term and period == "BEGINNING"),
            "WRONG_EXAM_TERM",
        )
        candidates = exam_students(cur, section_id, kind, term, starts)
        require(candidates, "NO_EXAM_CANDIDATES")

        capacities = []
        for room in rooms:
            capacity = value(cur, """SELECT exam_capacity FROM classroom
                                    WHERE room_code=%s AND is_active""", (room,))
            require(capacity and capacity > 0, "EXAM_ROOM_UNAVAILABLE")
            conflict = value(
                cur,
                """SELECT 1 FROM exam_room r JOIN exam e ON e.exam_id=r.exam_id
                   WHERE r.room_code=%s AND e.state='PUBLISHED' AND e.starts_at<%s AND e.ends_at>%s
                   LIMIT 1""",
                (room, ends, starts),
            )
            require(not conflict, "EXAM_ROOM_CONFLICT")
            capacities.append((room, capacity))
        require(sum(capacity for _, capacity in capacities) >= len(candidates), "EXAM_CAPACITY")

        candidate_nos = tuple(row["student_no"] for row in candidates)
        placeholders = ",".join(["%s"] * len(candidate_nos))
        conflict = value(
            cur,
            f"""SELECT 1 FROM exam e JOIN exam_seat s ON s.exam_id=e.exam_id
                JOIN enrollment n ON n.enrollment_id=s.enrollment_id
                WHERE e.state='PUBLISHED' AND s.candidate_state='SCHEDULED'
                  AND e.starts_at<%s AND e.ends_at>%s AND n.student_no IN ({placeholders}) LIMIT 1""",
            (ends, starts, *candidate_nos),
        )
        require(not conflict, "EXAM_STUDENT_CONFLICT")

        for teacher in invigilators.values():
            require(value(cur, "SELECT 1 FROM teacher WHERE teacher_no=%s AND is_active", (teacher,)),
                    "INACTIVE_INVIGILATOR")
            conflict = value(
                cur,
                """SELECT 1 FROM invigilation i JOIN exam e ON e.exam_id=i.exam_id
                   WHERE i.teacher_no=%s AND e.state='PUBLISHED' AND e.starts_at<%s AND e.ends_at>%s
                   LIMIT 1""",
                (teacher, ends, starts),
            )
            require(not conflict, "INVIGILATOR_CONFLICT")

        cur.execute(
            """INSERT INTO exam (section_id,scheduled_term_code,exam_period,attempt_kind,attempt_no,
                                 starts_at,ends_at,exam_method)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'笔试')""",
            (section_id, term, period, kind, attempt, starts, ends),
        )
        exam_id = cur.lastrowid
        cur.executemany("INSERT INTO exam_room VALUES (%s,%s,%s)",
                        [(exam_id, room, capacity) for room, capacity in capacities])
        seats = ((room, seat) for room, capacity in capacities for seat in range(1, capacity + 1))
        cur.executemany(
            """INSERT INTO exam_seat (exam_id,enrollment_id,room_code,seat_no,ticket_no)
               VALUES (%s,%s,%s,%s,%s)""",
            [(exam_id, candidate["enrollment_id"], room, seat,
              f"T{exam_id}-{serial}")
             for serial, (candidate, (room, seat)) in enumerate(zip(candidates, seats), 1)],
        )
        if invigilators:
            cur.executemany("INSERT INTO invigilation VALUES (%s,%s,%s,'LEAD')",
                            [(exam_id, room, teacher) for room, teacher in invigilators.items()])
        cur.execute("UPDATE exam SET state='PUBLISHED' WHERE exam_id=%s", (exam_id,))
        log(cur, account, "PUBLISH_EXAM", "EXAM", exam_id)
        return exam_id


def student_exams(cn, account, term):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        student_no = permission(cur, account, "EXAM_READ")["student_no"]
        return rows(
            cur,
            """SELECT e.exam_id,s.exam_seat_id,s.candidate_state,s.room_code,s.seat_no,s.ticket_no
               FROM exam e JOIN exam_seat s ON s.exam_id=e.exam_id
               JOIN enrollment n ON n.enrollment_id=s.enrollment_id
               WHERE e.state='PUBLISHED' AND e.scheduled_term_code=%s AND n.student_no=%s
               ORDER BY e.starts_at,e.exam_id""",
            (term, student_no),
        )


def teacher_invigilation(cn, account, term):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        teacher_no = permission(cur, account, "EXAM_INVIGILATION")["teacher_no"]
        return rows(
            cur,
            """SELECT e.exam_id,e.starts_at,e.ends_at,i.room_code,c.course_name,i.duty
               FROM invigilation i JOIN exam e ON e.exam_id=i.exam_id
               JOIN section s ON s.section_id=e.section_id
               JOIN course c ON c.course_code=s.course_code
               WHERE e.state='PUBLISHED' AND e.scheduled_term_code=%s AND i.teacher_no=%s
               ORDER BY e.starts_at,e.exam_id""",
            (term, teacher_no),
        )


def request_deferral(cn, account, seat_id, evidence_ref, now):
    with transaction(cn) as cur:
        student_no = permission(cur, account, "DEFERRAL")["student_no"]
        seat = one(
            cur,
            """SELECT e.starts_at,ex.candidate_state FROM exam_seat ex
               JOIN exam e ON e.exam_id=ex.exam_id
               JOIN enrollment n ON n.enrollment_id=ex.enrollment_id
               WHERE exam_seat_id=%s AND n.student_no=%s
                 AND e.state='PUBLISHED' AND e.attempt_kind='REGULAR' FOR UPDATE OF ex""",
            (seat_id, student_no),
        )
        require(seat is not None, "FORBIDDEN")
        require(seat["candidate_state"] == "SCHEDULED" and now < seat["starts_at"],
                "DEFERRAL_CLOSED")
        pending = value(
            cur,
            """SELECT r.request_id FROM request r JOIN deferral_req d ON d.request_id=r.request_id
               WHERE d.exam_seat_id=%s AND r.state IN ('PENDING','IN_REVIEW')""",
            (seat_id,),
        )
        if pending:
            return pending
        cur.execute(
            """INSERT INTO request (applicant_id,request_type,reason,requested_at)
               VALUES (%s,'DEFERRAL','测试缓考说明',%s)""", (account, now),
        )
        request_id = cur.lastrowid
        cur.execute("INSERT INTO deferral_req VALUES (%s,%s,%s)",
                    (request_id, seat_id, evidence_ref))
        return request_id


def approve_deferral(cn, account, request_id, now):
    with transaction(cn) as cur:
        permission(cur, account, "DEFERRAL_REVIEW")
        change = one(cur, """SELECT r.state,d.exam_seat_id FROM request r JOIN deferral_req d
                              ON d.request_id=r.request_id WHERE r.request_id=%s
                              AND r.request_type='DEFERRAL' FOR UPDATE OF r""", (request_id,))
        require(change is not None, "NO_REQUEST")
        if change["state"] == "APPROVED":
            return False
        require(change["state"] in {"PENDING", "IN_REVIEW"}, "REQUEST_FINAL")
        seat = one(cur, """SELECT s.candidate_state,e.starts_at,e.state FROM exam_seat s
                            JOIN exam e ON e.exam_id=s.exam_id WHERE s.exam_seat_id=%s
                            FOR UPDATE OF s""", (change["exam_seat_id"],))
        require(seat["state"] == "PUBLISHED" and seat["candidate_state"] == "SCHEDULED"
                and now < seat["starts_at"], "DEFERRAL_CLOSED")
        cur.execute("UPDATE exam_seat SET candidate_state='DEFERRED' WHERE exam_seat_id=%s",
                    (change["exam_seat_id"],))
        approve(cur, account, request_id)
        return True
