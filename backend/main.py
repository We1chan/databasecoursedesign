"""Application factory and HTTP trust boundary."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .errors import error, install_handlers
from .routes import router
from .security import CSRF_HEADER, LoginGate, now_utc
from .settings import Settings


def create_app(settings=None, *, connect=None, clock=now_utc):
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="教务系统 API",
        version="1.0.0",
        description="使用 HttpOnly 会话 Cookie。已登录的写请求需要 X-CSRF-Token。",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.connect = connect or settings.connect
    app.state.clock = clock
    app.state.login_gate = LoginGate()
    install_handlers(app)
    app.include_router(router)
    original_openapi = app.openapi

    def openapi_with_csrf():
        schema = original_openapi()
        for operations in schema["paths"].values():
            for method, definition in operations.items():
                if method in {"post", "put", "patch", "delete"} and definition.get("security"):
                    params = definition.setdefault("parameters", [])
                    if not any(p["name"] == CSRF_HEADER and p["in"] == "header" for p in params):
                        params.append({
                            "name": CSRF_HEADER, "in": "header", "required": True,
                            "schema": {"type": "string"},
                            "description": "使用登录或 GET /auth/me 返回的 csrf_token",
                        })
        return schema

    app.openapi = openapi_with_csrf

    @app.middleware("http")
    async def check_frontend_and_set_headers(request: Request, call_next):
        origin = request.headers.get("origin")
        own_origin = f"{request.url.scheme}://{request.url.netloc}"
        is_api = request.url.path.startswith("/api/v1/")
        if is_api and origin and request.method not in {"GET", "HEAD", "OPTIONS"} and (
            origin != own_origin and origin not in settings.frontend_origins
        ):
            response = error(403, "ORIGIN_DENIED", "请求来源不在允许名单")
        elif request.url.path == "/api/v1/auth/login" and request.method == "POST" and (
            request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
        ):
            response = error(415, "UNSUPPORTED_MEDIA_TYPE", "登录使用 application/json")
        else:
            response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if is_api:
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    if settings.frontend_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.frontend_origins), allow_credentials=True,
            allow_methods=["GET","POST","PUT","PATCH","DELETE"],
            allow_headers=["Content-Type",CSRF_HEADER],
        )
    return app


app = create_app()
