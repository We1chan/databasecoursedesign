"""Runtime configuration for a local or same-site course API."""

from dataclasses import dataclass, field
from os import environ
from urllib.parse import urlsplit

from .database import connect_mysql


@dataclass(frozen=True)
class Settings:
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "course_design"
    db_user: str = "course_app"
    db_password: str = field(default="", repr=False)
    data_key: str = field(default="", repr=False)
    frontend_origins: tuple[str, ...] = ()
    secure_cookie: bool = True

    @classmethod
    def from_env(cls):
        origins = tuple(
            part.strip().rstrip("/")
            for part in environ.get("COURSE_FRONTEND_ORIGINS", "").split(",") if part.strip()
        )
        for origin in origins:
            url = urlsplit(origin)
            if url.scheme not in {"http", "https"} or not url.hostname or (
                url.path or url.fragment or url.query or url.username or "*" in origin
            ):
                raise ValueError("COURSE_FRONTEND_ORIGINS 必须是完整的 HTTP(S) Origin")
        return cls(
            db_host=environ.get("COURSE_DB_HOST", "127.0.0.1"),
            db_port=int(environ.get("COURSE_DB_PORT", "3306")),
            db_name=environ.get("COURSE_DB_NAME", "course_design"),
            db_user=environ.get("COURSE_DB_USER", "course_app"),
            db_password=environ.get("COURSE_DB_PASSWORD", ""),
            data_key=environ.get("COURSE_DATA_KEY", ""),
            frontend_origins=origins,
            secure_cookie=environ.get("COURSE_COOKIE_SECURE", "1") != "0",
        )

    def connect(self):
        return connect_mysql(
            host=self.db_host, port=self.db_port, user=self.db_user,
            password=self.db_password, database=self.db_name,
        )
