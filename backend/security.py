"""Opaque database-backed sessions, CSRF validation and frontend cookie settings."""

from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
from secrets import token_urlsafe
from threading import Lock
from time import monotonic
from typing import Annotated, Any
from unicodedata import combining, normalize

from fastapi import Depends, Request
from fastapi.security import APIKeyCookie

from .accounts import verify_login
from .database import one, permission, rows, transaction
from .errors import Fault


COOKIE = "course_session"
COOKIE_PATH = "/api/v1"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL = timedelta(hours=8)
cookie_auth = APIKeyCookie(name=COOKIE, scheme_name="CourseSession", auto_error=False)


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_connection(request: Request):
    with request.app.state.connect() as cn:
        yield cn


Connection = Annotated[Any, Depends(get_connection)]


def digest(token):
    return sha256(token.encode("ascii")).digest()


def csrf_token(token):
    return hmac.digest(token.encode("ascii"), b"course-api-csrf-v1", "sha256").hex()


def account_view(cur, account_id):
    actor = one(cur, """SELECT account_id, login_name, student_no, teacher_no, account_state
                        FROM user_account WHERE account_id=%s""", (account_id,))
    if actor is not None:
        actor["roles"] = [
            row["role_code"] for row in rows(
                cur, "SELECT role_code FROM user_role WHERE account_id=%s ORDER BY role_code",
                (account_id,),
            )
        ]
    return actor


def current_session(
    request: Request,
    cn: Connection,
    token: Annotated[str | None, Depends(cookie_auth)],
):
    if not token or len(token) != 43 or not token.isascii():
        raise Fault(401, "UNAUTHENTICATED", "请先登录")
    now = request.app.state.clock()
    with cn.cursor(dictionary=True, buffered=True) as cur:
        session = one(
            cur,
            """SELECT s.account_id FROM auth_session s
               JOIN user_account a ON a.account_id=s.account_id
               WHERE s.session_digest=%s AND s.expires_at>%s AND a.account_state='ACTIVE'""",
            (digest(token), now),
        )
        if session is None:
            raise Fault(401, "UNAUTHENTICATED", "登录会话已失效")
        actor = account_view(cur, session["account_id"])
    csrf = csrf_token(token)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        provided = request.headers.get(CSRF_HEADER, "")
        if not provided.isascii() or not hmac.compare_digest(provided, csrf):
            raise Fault(403, "INVALID_CSRF", "写操作缺少有效的 CSRF 值")
    return {**actor, "csrf_token": csrf}


Actor = Annotated[dict, Depends(current_session)]


def has_permission(cn, actor, code):
    with cn.cursor(dictionary=True, buffered=True) as cur:
        return permission(cur, actor["account_id"], code)


def start_session(cn, login, password, now):
    token = token_urlsafe(32)
    with transaction(cn) as cur:
        account = verify_login(cur, login, password, now)
        cur.execute("DELETE FROM auth_session WHERE account_id=%s AND expires_at<=%s",
                    (account, now))
        cur.execute("INSERT INTO auth_session VALUES (%s,%s,%s)",
                    (digest(token), account, now + SESSION_TTL))
        actor = account_view(cur, account)
    return token, {**actor, "csrf_token": csrf_token(token)}


def end_session(cn, token):
    with transaction(cn) as cur:
        cur.execute("DELETE FROM auth_session WHERE session_digest=%s", (digest(token),))


def set_session_cookie(response, request, token):
    response.set_cookie(
        key=COOKIE, value=token, max_age=int(SESSION_TTL.total_seconds()),
        path=COOKIE_PATH, secure=request.app.state.settings.secure_cookie,
        httponly=True, samesite="strict",
    )


class LoginGate:
    """Bounded login admission per client and account name for the local server."""

    def __init__(self):
        self.lock = Lock()
        self.clients = OrderedDict()

    def observe(self, client, login):
        # Match common case and accent equivalents of the MySQL login collation.
        normalized = normalize("NFKD", login.casefold())
        key = (client, "".join(char for char in normalized if not combining(char)))
        now = monotonic()
        with self.lock:
            times = self.clients.setdefault(key, deque())
            self.clients.move_to_end(key)
            if len(self.clients) > 4096:
                self.clients.popitem(last=False)
            while times and times[0] <= now - 60:
                times.popleft()
            if len(times) >= 20:
                raise Fault(429, "LOGIN_RATE_LIMITED", "登录请求过多，请稍后再试",
                            {"Retry-After": "60"})
            times.append(now)
