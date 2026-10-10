"""Сборка FastAPI: создание таблиц на старте, монтаж роутов, /health."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import create_all, dispose_engine
from app.routers import admin, auth, operator, public
from app.schemas import HealthOut

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


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

# Роуты: публичная часть, кабинет оператора, админ, OIDC.
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(operator.router)
app.include_router(public.router)


@app.get("/health", response_model=HealthOut, tags=["infra"])
async def health() -> HealthOut:
    """Живость процесса. БД здесь не трогаем — проверка должна быть дешёвой."""
    return HealthOut(
        status="ok",
        app=settings.app_name,
        model_provider=settings.model_provider,
    )
