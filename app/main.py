"""Сборка FastAPI: создание таблиц на старте, монтаж роутов, /health."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import create_all, dispose_engine
from app.http_charset import JsonCharsetMiddleware
from app.routers import admin, auth, files, operator, public
from app.schemas import HealthOut

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# В БД время в UTC, а агентство и соискатели живут по Москве. Фиксированный
# сдвиг, а не zoneinfo: в slim-образе нет tzdata, а перехода на летнее время
# в РФ нет.
_MSK = timezone(timedelta(hours=3))


def _format_dt(value: datetime, fmt: str = "%d.%m.%Y %H:%M") -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_MSK).strftime(fmt)


templates.env.filters["dt"] = _format_dt


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Старт: таблицы + каталог хранилища. Остановка: закрыть пул."""
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    await create_all()
    log.info("started: provider=%s", settings.model_provider)
    try:
        yield
    finally:
        await dispose_engine()


app = FastAPI(
    title="Приём и разбор документов соискателей",
    version="0.1.0",
    lifespan=lifespan,
)

app.state.templates = templates
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# Сессия кабинета (OIDC): подписанная cookie, секрет — SESSION_SECRET.
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")

# JSON всегда с charset=utf-8: PowerShell 5.1 без него читает ответ как Latin-1.
app.add_middleware(JsonCharsetMiddleware)

# Роуты: публичная часть, кабинет оператора, админ, OIDC.
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(operator.router)
app.include_router(files.router)
app.include_router(public.router)


@app.exception_handler(StarletteHTTPException)
async def login_redirect(request: Request, exc: StarletteHTTPException):
    # Страницы кабинета без входа ведут на Keycloak; /api/* остаётся 401 для htmx и проверок.
    path = request.url.path
    if exc.status_code == 401 and not path.startswith("/api/"):
        return RedirectResponse(f"/auth/login?next={quote(path)}", status_code=303)
    return await http_exception_handler(request, exc)


@app.get("/health", response_model=HealthOut, tags=["infra"])
async def health() -> HealthOut:
    """Живость процесса. БД здесь не трогаем — проверка должна быть дешёвой."""
    return HealthOut(
        status="ok",
        app=settings.app_name,
        model_provider=settings.model_provider,
    )
