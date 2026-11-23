"""Disposable schemas for integration tests and the standalone benchmark."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

# Preserve the documented invocation `python tests/benchmark.py`.
if not __package__:
    repo = str(Path(__file__).resolve().parents[1])
    if repo not in sys.path:
        sys.path.insert(0, repo)

from backend.database import AUTH_SESSIONS, QUEUE_INDEX, SCHEMA, Rejected, connect_mysql, rows, script, value


def connect(database=None):
    return connect_mysql(
        host=os.environ["DB_TEST_HOST"],
        port=int(os.environ["DB_TEST_PORT"]),
        user=os.environ["DB_TEST_USER"],
        password=os.environ.get("DB_TEST_PASSWORD", ""),
        database=database,
    )


@contextmanager
def sandbox(*, optimized=False, api=False):
    """Create an unused, randomly named schema and drop only that schema."""
    database = "course_test_" + uuid4().hex
    with connect() as server:
        with server.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        try:
            with connect(database) as cn:
                script(cn, SCHEMA.read_text(encoding="utf8"))
                if optimized:
                    script(cn, QUEUE_INDEX.read_text(encoding="utf8"))
                if api:
                    script(cn, AUTH_SESSIONS.read_text(encoding="utf8"))
                yield cn
        finally:
            with server.cursor() as cur:
                cur.execute(f"DROP DATABASE `{database}`")
