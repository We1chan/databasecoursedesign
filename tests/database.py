"""Disposable schemas and parameterized helpers for the MySQL integration tests."""

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import mysql.connector


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql" / "02-schema.sql"
QUEUE_INDEX = ROOT / "sql" / "03-request-queue-index.sql"


class Rejected(Exception):
    """Expected business rejection. The operation's transaction rolls back."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def connect(database=None):
    cn = mysql.connector.connect(
        host=os.environ["DB_TEST_HOST"],
        port=int(os.environ["DB_TEST_PORT"]),
        user=os.environ["DB_TEST_USER"],
        password=os.environ.get("DB_TEST_PASSWORD", ""),
        database=database,
        charset="utf8mb4",
        time_zone="+00:00",
        autocommit=True,
        connection_timeout=10,
    )
    return cn


def script(cn, sql):
    with cn.cursor() as cur:
        cur.execute(sql)
        while True:
            if cur.with_rows:
                cur.fetchall()
            if not cur.nextset():
                break


@contextmanager
def sandbox(*, optimized=False):
    """Create an unused, randomly named schema and drop only that schema."""
    database = "course_test_" + uuid4().hex
    with connect() as server:
        with server.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        try:
            with connect(database) as cn:
                script(cn, SCHEMA.read_text(encoding="utf8"))
                if optimized:
                    script(cn, QUEUE_INDEX.read_text(encoding="utf8"))
                yield cn
        finally:
            with server.cursor() as cur:
                cur.execute(f"DROP DATABASE `{database}`")


@contextmanager
def transaction(cn):
    cn.start_transaction(isolation_level="READ COMMITTED")
    try:
        with cn.cursor(dictionary=True, buffered=True) as cur:
            yield cur
        cn.commit()
    except BaseException:
        cn.rollback()
        raise


def rows(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def value(cur, sql, args=()):
    row = one(cur, sql, args)
    return next(iter(row.values())) if row is not None else None


def permission(cur, account, permission_code):
    actor = one(
        cur,
        """SELECT u.account_id, u.student_no, u.teacher_no
           FROM user_account u
           WHERE u.account_id=%s AND u.account_state='ACTIVE'
             AND EXISTS (
                 SELECT 1 FROM user_role ur
                 JOIN role_permission rp ON rp.role_code=ur.role_code
                 WHERE ur.account_id=u.account_id AND rp.permission_code=%s
             )""",
        (account, permission_code),
    )
    require(actor is not None, "FORBIDDEN")
    return actor


def log(cur, account, action, target_kind, target_ref, result="OK"):
    cur.execute(
        """INSERT INTO operation_log
           (actor_id,action_code,target_kind,target_ref,result_code)
           VALUES (%s,%s,%s,%s,%s)""",
        (account, action, target_kind, str(target_ref), result),
    )
