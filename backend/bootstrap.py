"""Install the backend schema and provision the first administrator."""

import argparse
from getpass import getpass
from os import environ

from .accounts import password_record
from .database import AUTH_SESSIONS, QUEUE_INDEX, SCHEMA, one, require, script, transaction, value
from .settings import Settings


PERMISSIONS = {
    "admin": ("ADMISSION", "PROFILE_REVIEW", "SCHEDULE", "ROUND", "EXAM",
              "DEFERRAL_REVIEW", "GRADE_WINDOW", "GRADE_PUBLISH", "CORRECTION_REVIEW", "EVAL_ADMIN",
              "ACCOUNT_ADMIN", "ROLE_ADMIN", "CATALOG"),
    "student": ("SELF_PROFILE", "SELECT_COURSE", "EXAM_READ", "DEFERRAL", "GRADE_READ", "EVAL_SUBMIT"),
    "teacher": ("GRADE_WRITE", "CORRECTION_SUBMIT", "EVAL_READ", "ROSTER_READ", "EXAM_INVIGILATION"),
}
ROLE_NAMES = {"admin": "管理员", "student": "学生", "teacher": "任课教师"}


def install_schema(cn):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        exists = value(cur, """SELECT COUNT(*) FROM information_schema.tables
                              WHERE table_schema=DATABASE() AND table_name='user_account'""")
        if not exists:
            require(not value(cur, """SELECT COUNT(*) FROM information_schema.tables
                                     WHERE table_schema=DATABASE()"""), "NONEMPTY_UNKNOWN_SCHEMA")
            script(cn, SCHEMA.read_text(encoding="utf8"))
        indexed = value(cur, """SELECT COUNT(*) FROM information_schema.statistics
                               WHERE table_schema=DATABASE() AND table_name='request'
                                AND index_name='ix_request_queue_time'""")
        if not indexed:
            script(cn, QUEUE_INDEX.read_text(encoding="utf8"))
        script(cn, AUTH_SESSIONS.read_text(encoding="utf8"))


def provision_admin(cn, login, password):
    with transaction(cn) as cur:
        for role, codes in PERMISSIONS.items():
            cur.execute("INSERT IGNORE INTO role (role_code,role_name) VALUES (%s,%s)",
                        (role, ROLE_NAMES[role]))
            cur.executemany("INSERT IGNORE INTO permission VALUES (%s,%s)", [(code, code) for code in codes])
            cur.executemany("INSERT IGNORE INTO role_permission VALUES (%s,%s)",
                            [(role, code) for code in codes])
        existing = one(cur, "SELECT account_id FROM user_account WHERE login_name=%s FOR UPDATE", (login,))
        if existing:
            account = existing["account_id"]
            require(value(cur, "SELECT 1 FROM user_role WHERE account_id=%s AND role_code='admin'",
                          (account,)), "EXISTING_ACCOUNT_NOT_ADMIN")
            return account
        cur.execute("INSERT INTO user_account (login_name,password_hash) VALUES (%s,%s)",
                    (login, password_record(password)))
        account = cur.lastrowid
        cur.execute("INSERT INTO user_role VALUES (%s,'admin')", (account,))
        return account


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", required=True)
    args = parser.parse_args()
    if not args.admin.strip() or len(args.admin) > 64:
        parser.error("账号长度必须为 1–64 个字符")
    password = environ.get("COURSE_ADMIN_PASSWORD") or getpass("初始管理员密码（至少 12 字符）: ")
    if not 12 <= len(password) <= 128:
        parser.error("密码长度必须为 12–128 个字符")
    with Settings.from_env().connect() as cn:
        install_schema(cn)
        account = provision_admin(cn, args.admin.strip(), password)
    print(f"接口数据库已初始化，管理员 ID {account}")


if __name__ == "__main__":
    main()
