"""Authentication-related database operations for the disposable test application."""

from hashlib import scrypt
from hmac import compare_digest
from secrets import token_bytes

from database import log, one, permission, require, transaction


def digest(password, salt):
    return scrypt(password.encode("utf8"), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024**2)


def password_record(password):
    require(len(password) >= 12, "PASSWORD_TOO_SHORT")
    salt = token_bytes(16)
    return f"scrypt${salt.hex()}${digest(password, salt).hex()}"


def matches(password, record):
    try:
        scheme, salt, expected = record.split("$")
        return scheme == "scrypt" and compare_digest(digest(password, bytes.fromhex(salt)),
                                                    bytes.fromhex(expected))
    except ValueError:
        return False


def add_student_account(cn, administrator, login, student_no, password):
    with transaction(cn) as cur:
        permission(cur, administrator, "ACCOUNT_ADMIN")
        cur.execute(
            """INSERT INTO user_account (login_name,password_hash,student_no)
               VALUES (%s,%s,%s)""", (login, password_record(password), student_no),
        )
        account = cur.lastrowid
        log(cur, administrator, "CREATE_ACCOUNT", "USER_ACCOUNT", account)
        return account


def set_role(cn, administrator, account, role, *, enabled):
    with transaction(cn) as cur:
        permission(cur, administrator, "ROLE_ADMIN")
        require(one(cur, "SELECT account_id FROM user_account WHERE account_id=%s FOR UPDATE",
                    (account,)), "NO_ACCOUNT")
        require(one(cur, "SELECT role_code FROM role WHERE role_code=%s", (role,)), "NO_ROLE")
        if enabled:
            cur.execute(
                """INSERT INTO user_role VALUES (%s,%s)
                   ON DUPLICATE KEY UPDATE account_id=user_role.account_id""", (account, role),
            )
        else:
            cur.execute("DELETE FROM user_role WHERE account_id=%s AND role_code=%s", (account, role))
        log(cur, administrator, "GRANT_ROLE" if enabled else "REVOKE_ROLE", "USER_ACCOUNT", account)


def lock_account(cn, administrator, account):
    with transaction(cn) as cur:
        permission(cur, administrator, "ACCOUNT_ADMIN")
        cur.execute("UPDATE user_account SET account_state='LOCKED' WHERE account_id=%s", (account,))
        require(cur.rowcount == 1, "ACCOUNT_UNAVAILABLE")
        log(cur, administrator, "LOCK_ACCOUNT", "USER_ACCOUNT", account)


def authenticate(cn, login, password, now):
    with transaction(cn) as cur:
        account = one(
            cur,
            """SELECT account_id,password_hash,account_state FROM user_account
               WHERE login_name=%s FOR UPDATE""", (login,),
        )
        require(account and account["account_state"] == "ACTIVE"
                and matches(password, account["password_hash"]), "INVALID_CREDENTIALS")
        cur.execute("UPDATE user_account SET last_login_at=%s WHERE account_id=%s",
                    (now, account["account_id"]))
        return account["account_id"]


def change_password(cn, account, old_password, new_password):
    with transaction(cn) as cur:
        user = one(cur, """SELECT password_hash,account_state FROM user_account
                           WHERE account_id=%s FOR UPDATE""", (account,))
        require(user and user["account_state"] == "ACTIVE"
                and matches(old_password, user["password_hash"]), "INVALID_CREDENTIALS")
        cur.execute("UPDATE user_account SET password_hash=%s WHERE account_id=%s",
                    (password_record(new_password), account))
        log(cur, account, "CHANGE_PASSWORD", "USER_ACCOUNT", account)
