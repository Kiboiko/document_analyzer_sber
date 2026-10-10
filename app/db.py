"""Асинхронный доступ к PostgreSQL: engine, фабрика сессий, Base."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей."""


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
)

session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: сессия на запрос."""
    async with session_factory() as session:
        yield session


async def create_all() -> None:
    """Создать таблицы на старте (TZ: без миграций, `Base.metadata.create_all`)."""
    import app.models  # noqa: F401  — регистрация мапперов до create_all

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Закрыть пул соединений (shutdown приложения/воркера)."""
    await engine.dispose()
