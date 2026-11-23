"""Connection setup and shared MySQL transaction operations."""

from contextlib import contextmanager
from pathlib import Path

import mysql.connector


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "sql" / "02-schema.sql"
QUEUE_INDEX = ROOT / "sql" / "03-request-queue-index.sql"
AUTH_SESSIONS = ROOT / "sql" / "05-auth-sessions.sql"


class Rejected(Exception):
    """A business rejection. The enclosing transaction rolls back."""


def require(condition, code):
    if not condition:
        raise Rejected(code)


def connect_mysql(*, host, port, user, password, database=None):
    return mysql.connector.connect(
        host=host, port=port, user=user, password=password, database=database,
        charset="utf8mb4", time_zone="+00:00", autocommit=True, connection_timeout=5,
    )


def script(cn, sql):
    with cn.cursor() as cur:
        cur.execute(sql)
        while True:
            if cur.with_rows:
                cur.fetchall()
            if not cur.nextset():
                break


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
