"""FastAPI 依赖（SessionLocal 等运行态挂在 app.state 上）。"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.SessionLocal() as s:
        yield s
