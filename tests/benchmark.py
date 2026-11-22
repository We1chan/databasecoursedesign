"""Seed an approval workload and measure the same queue query before/after indexing."""

import argparse
from datetime import date, datetime, timedelta
from hashlib import sha256
from math import ceil
from statistics import median
from time import perf_counter

from database import QUEUE_INDEX, sandbox, script
from fixtures import seed_reference


QUERY = """SELECT request_id,request_type,applicant_id,requested_at
           FROM request WHERE state='PENDING'
           ORDER BY requested_at,request_id LIMIT 50"""
TYPED_QUERY = QUERY.replace("state='PENDING'", "state='PENDING' AND request_type='RETAKE'")
STATES = ("PENDING", "APPROVED", "APPROVED", "REJECTED", "WITHDRAWN")


def populate_queue(cn, amount):
    applicant_count = min(8_000, amount)
    first_date = datetime(2026, 9, 1)
    with cn.cursor() as cur:
        cur.execute(
            """INSERT INTO import_batch (source_system,source_batch_no,content_sha256,state)
               VALUES ('BENCHMARK','QUEUE-BATCH',%s,'IMPORTED')""",
            (sha256(b"fixture-course-stage3-queue-v1").digest(),),
        )
        batch_id = cur.lastrowid
        for start in range(0, applicant_count, 1_000):
            batch = range(start, min(start + 1_000, applicant_count))
            cur.executemany(
                """INSERT INTO student (student_no,student_name,class_no,admitted_on,batch_id)
                   VALUES (%s,%s,'2026010001',%s,%s)""",
                [(f"2024{i:06d}", f"申请样例{i}", date(2026, 9, 1), batch_id) for i in batch],
            )
            cur.executemany(
                """INSERT INTO user_account (account_id,login_name,password_hash,student_no)
                   VALUES (%s,%s,'fixture-disabled-password',%s)""",
                [(10_000 + i, f"bench-{i}", f"2024{i:06d}") for i in batch],
            )

        for start in range(0, amount, 1_000):
            batch = range(start, min(start + 1_000, amount))
            cur.executemany(
                """INSERT INTO request (request_id,applicant_id,request_type,state,reason,requested_at)
                   VALUES (%s,%s,%s,%s,'虚构批量测试数据',%s)""",
                [(i + 1, 10_000 + i % applicant_count, "INFO_CHANGE" if i % 2 == 0 else "RETAKE",
                  STATES[i % len(STATES)], first_date + timedelta(minutes=i // 25)) for i in batch],
            )
            info_ids = [(i + 1, b"fixture_sealed_phone") for i in batch if i % 2 == 0]
            retake_ids = [(i + 1,) for i in batch if i % 2]
            if info_ids:
                cur.executemany(
                    """INSERT INTO info_change_item (request_id,field_code,new_value_cipher)
                       VALUES (%s,'PHONE',%s)""", info_ids,
                )
            if retake_ids:
                cur.executemany(
                    """INSERT INTO retake_req (request_id,course_code,term_code,study_type)
                       VALUES (%s,'DB','2026-2027-1','RETAKE')""", retake_ids,
                )

        cur.execute("ANALYZE TABLE request")
        cur.fetchall()
        cur.execute("SELECT COUNT(*),SUM(state='PENDING') FROM request")
        total, pending = cur.fetchone()
        if total != amount or pending != (amount + 4) // 5:
            raise AssertionError((total, pending))
        print(f"Dataset: {total:,} requests, {pending:,} pending, {applicant_count:,} accounts")
        cur.execute("SELECT request_type,COUNT(*) FROM request WHERE state='PENDING' GROUP BY request_type")
        print(f"Pending by type: {cur.fetchall()}")


def measure(cn, query, repeats):
    samples = []
    with cn.cursor() as cur:
        for _ in range(5):
            cur.execute(query)
            cur.fetchall()
        for _ in range(repeats):
            start = perf_counter()
            cur.execute(query)
            result = cur.fetchall()
            samples.append((perf_counter() - start) * 1_000)
        cur.execute(f"EXPLAIN ANALYZE {query}")
        plan = cur.fetchone()[0].strip()
    samples.sort()
    return {
        "median": median(samples),
        "p95": samples[ceil(repeats * .95) - 1],
        "min": samples[0],
        "max": samples[-1],
        "plan": plan,
        "result": result,
    }


def print_result(label, data, repeats):
    print(f"[{label}] warmed 5 + measured {repeats}; "
          f"median={data['median']:.3f} ms, p95={data['p95']:.3f} ms, "
          f"min={data['min']:.3f} ms, max={data['max']:.3f} ms")
    print(data["plan"])


def run(amount, repeats, baseline_only=False):
    with sandbox() as cn:
        seed_reference(cn)
        with cn.cursor() as cur:
            cur.execute("SELECT VERSION(),@@innodb_buffer_pool_size,@@sql_mode")
            print(f"MySQL / buffer pool bytes / SQL mode: {cur.fetchone()}")
        populate_queue(cn, amount)
        before = measure(cn, QUERY, repeats)
        before_typed = measure(cn, TYPED_QUERY, repeats)
        print_result("before / all pending requests", before, repeats)
        print_result("before / retake-only pending requests", before_typed, repeats)
        if baseline_only:
            return
        script(cn, QUEUE_INDEX.read_text(encoding="utf8"))
        script(cn, "ANALYZE TABLE request")
        after = measure(cn, QUERY, repeats)
        after_typed = measure(cn, TYPED_QUERY, repeats)
        print_result("after / all pending requests", after, repeats)
        print_result("after / retake-only pending requests", after_typed, repeats)
        if before["result"] != after["result"] or before_typed["result"] != after_typed["result"]:
            raise AssertionError("Queue result or ordering changed after indexing")
        if len(after["result"]) != 50 or len(after_typed["result"]) != 50:
            raise AssertionError("The benchmark did not return a full page")
        print("Both 50-row pages have identical contents and ordering before/after.")
        print(f"All-pending median speed-up: {before['median'] / after['median']:.2f}x")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=120_000)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--baseline-only", action="store_true")
    args = parser.parse_args()
    if args.rows < 500 or args.repeats < 5:
        parser.error("Supply at least 500 requests and 5 measured runs")
    run(args.rows, args.repeats, args.baseline_only)
