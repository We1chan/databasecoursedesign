"""Evaluation batches, writes, frozen aggregates and teacher-scope queries."""

from decimal import Decimal, ROUND_HALF_UP

from database import log, one, permission, require, rows, transaction, value


def open_evaluation(cn, account, term, starts, ends, indicators):
    require(indicators and sum(weight for _, weight, _ in indicators) == 100, "EVAL_WEIGHTS")
    require(starts < ends, "INVALID_EVAL_WINDOW")
    with transaction(cn) as cur:
        permission(cur, account, "EVAL_ADMIN")
        require(one(cur, "SELECT term_code FROM term WHERE term_code=%s FOR UPDATE", (term,)),
                "NO_TERM")
        cur.execute("""INSERT INTO eval_scheme (scheme_code,version_no,scheme_name)
                       VALUES ('FIXTURE',1,'课堂评价')""")
        scheme = cur.lastrowid
        cur.executemany(
            """INSERT INTO eval_indicator (scheme_id,indicator_text,weight_percent,max_score,order_no)
               VALUES (%s,%s,%s,%s,%s)""",
            [(scheme, name, weight, maximum, index)
             for index, (name, weight, maximum) in enumerate(indicators, 1)],
        )
        cur.execute("""INSERT INTO eval_batch
                       (term_code,scheme_id,batch_name,category,opens_at,closes_at,state)
                       VALUES (%s,%s,'期末评价','课堂',%s,%s,'OPEN')""",
                    (term, scheme, starts, ends))
        batch_id = cur.lastrowid
        cur.execute(
            """INSERT INTO eval_target (batch_id,section_id,teacher_no)
               SELECT %s,s.section_id,t.teacher_no FROM section s JOIN section_teacher t
                 ON t.section_id=s.section_id WHERE s.term_code=%s AND EXISTS (
                   SELECT 1 FROM enrollment e WHERE e.section_id=s.section_id
                    AND e.state IN ('ENROLLED','COMPLETED')
                 ) ORDER BY s.section_id,t.teacher_no""",
            (batch_id, term),
        )
        cur.execute(
            """INSERT INTO eval_assignment (target_id,enrollment_id)
               SELECT t.target_id,e.enrollment_id FROM eval_target t JOIN enrollment e
                 ON e.section_id=t.section_id
               WHERE t.batch_id=%s AND e.state IN ('ENROLLED','COMPLETED')
               ORDER BY t.target_id,e.enrollment_id""",
            (batch_id,),
        )
        log(cur, account, "OPEN_EVALUATION", "EVAL_BATCH", batch_id)
        return batch_id


def submit_evaluation(cn, account, batch_id, assignment_id, scores, now, comment=None):
    with transaction(cn) as cur:
        student_no = permission(cur, account, "EVAL_SUBMIT")["student_no"]
        batch = one(cur, "SELECT * FROM eval_batch WHERE batch_id=%s FOR SHARE", (batch_id,))
        require(batch and batch["state"] == "OPEN" and batch["opens_at"] <= now < batch["closes_at"],
                "EVAL_CLOSED")
        assignment = one(
            cur,
            """SELECT a.assignment_id FROM eval_assignment a JOIN eval_target t
                 ON t.target_id=a.target_id JOIN enrollment e ON a.enrollment_id=e.enrollment_id
               WHERE t.batch_id=%s AND a.assignment_id=%s AND e.student_no=%s FOR UPDATE OF a""",
            (batch_id, assignment_id, student_no),
        )
        require(assignment, "FORBIDDEN")
        require(not value(cur, "SELECT 1 FROM evaluation WHERE assignment_id=%s", (assignment_id,)),
                "EVAL_ALREADY_SUBMITTED")
        criteria = {r["indicator_id"]: (r["max_score"], r["weight_percent"])
                    for r in rows(cur, "SELECT * FROM eval_indicator WHERE scheme_id=%s",
                                  (batch["scheme_id"],))}
        require(scores.keys() == criteria.keys(), "EVAL_WRONG_INDICATORS")
        total = Decimal(0)
        for identifier, score in scores.items():
            maximum, weight = criteria[identifier]
            require(0 <= score <= maximum, "EVAL_SCORE_RANGE")
            total += Decimal(score) / maximum * weight
        total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        cur.execute(
            """INSERT INTO evaluation (assignment_id,total_score,comment_text,submitted_at)
               VALUES (%s,%s,%s,%s)""",
            (assignment_id, total, comment, now),
        )
        cur.executemany("INSERT INTO eval_answer VALUES (%s,%s,%s)",
                        [(assignment_id, key, score) for key, score in scores.items()])
        return total


def finish_evaluation(cn, account, batch_id, now):
    with transaction(cn) as cur:
        permission(cur, account, "EVAL_ADMIN")
        batch = one(cur, "SELECT * FROM eval_batch WHERE batch_id=%s FOR UPDATE", (batch_id,))
        require(batch is not None, "NO_EVAL_BATCH")
        if batch["state"] == "AGGREGATED":
            return False
        require(batch["state"] == "OPEN" and now >= batch["closes_at"], "EVAL_STILL_OPEN")
        cur.execute("UPDATE eval_batch SET state='CLOSED' WHERE batch_id=%s", (batch_id,))
        cur.execute(
            """INSERT INTO eval_result (target_id,eligible_count,response_count,average_score,aggregated_at)
               SELECT t.target_id,COUNT(a.assignment_id),COUNT(e.assignment_id),AVG(e.total_score),%s
               FROM eval_target t LEFT JOIN eval_assignment a ON a.target_id=t.target_id
               LEFT JOIN evaluation e ON e.assignment_id=a.assignment_id
               WHERE t.batch_id=%s GROUP BY t.target_id""",
            (now, batch_id),
        )
        cur.execute(
            """INSERT INTO eval_indicator_result (target_id,indicator_id,average_score)
               SELECT t.target_id,r.indicator_id,AVG(r.score)
               FROM eval_target t JOIN eval_assignment a ON a.target_id=t.target_id
               JOIN eval_answer r ON r.assignment_id=a.assignment_id
               WHERE t.batch_id=%s GROUP BY t.target_id,r.indicator_id""",
            (batch_id,),
        )
        cur.execute("UPDATE eval_batch SET state='AGGREGATED' WHERE batch_id=%s", (batch_id,))
        log(cur, account, "FINISH_EVALUATION", "EVAL_BATCH", batch_id)
        return True


def teacher_results(cn, account, batch_id, minimum_responses):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        teacher_no = permission(cur, account, "EVAL_READ")["teacher_no"]
        return rows(cur, """SELECT * FROM v_teacher_eval_result
                            WHERE teacher_no=%s AND batch_id=%s AND response_count>=%s
                            ORDER BY section_id,indicator_id""",
                    (teacher_no, batch_id, minimum_responses))
