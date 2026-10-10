"""Кабинет оператора за OIDC (TZ 1, 5). Без входа PII не отдаём.

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.enums import Country, DocStatus
from app.schemas import (
    ApplicationCard,
    ApplicationListOut,
    CurrentUser,
    FieldOut,
    FieldPatch,
    QueueStats,
    ReparseResult,
)

router = APIRouter(tags=["operator"])


async def require_operator(request: Request) -> CurrentUser:
    """Зависимость: пускать только с ролью operator или admin.

    TODO(Кирилл): читать OIDC-сессию, 401/403 на сервере, не на клиенте.
    """
    raise NotImplementedError


# --------------------------------------------------------------------------- #
# Страницы кабинета
# --------------------------------------------------------------------------- #


@router.get("/operator", response_class=HTMLResponse)
async def page_applications(
    request: Request,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Список заявок с фильтрами (страна, статус, поиск)."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get("/operator/applications/{application_id}", response_class=HTMLResponse)
async def page_application_card(
    request: Request,
    application_id: uuid.UUID,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Карточка: оригинал рядом с полями, замечания, таймлайн истории."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get("/operator/queue", response_class=HTMLResponse)
async def page_queue(
    request: Request,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Раздел «в работе»: что ждёт, что разбирается, среднее время."""
    raise NotImplementedError  # TODO(Кирилл)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@router.get("/api/operator/applications", response_model=ApplicationListOut)
async def list_applications(
    country: Country | None = None,
    status: DocStatus | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> ApplicationListOut:
    """Список заявок с фильтрами."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get(
    "/api/operator/applications/{application_id}",
    response_model=ApplicationCard,
)
async def get_application_card(
    application_id: uuid.UUID,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> ApplicationCard:
    """Карточка заявки: оригиналы, поля, события, замечания."""
    raise NotImplementedError  # TODO(Кирилл)


@router.patch(
    "/api/operator/documents/{document_id}/fields/{field_id}",
    response_model=FieldOut,
)
async def patch_field(
    document_id: uuid.UUID,
    field_id: uuid.UUID,
    payload: FieldPatch,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> FieldOut:
    """Правка поля: source=operator, перепроверка правил, Event(field_corrected)."""
    raise NotImplementedError  # TODO(Кирилл)


@router.post(
    "/api/operator/documents/{document_id}/reparse",
    response_model=ReparseResult,
)
async def reparse_document(
    document_id: uuid.UUID,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> ReparseResult:
    """Повторный разбор: новый Job(pending) + Event(reparse_requested)."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get("/api/operator/queue", response_model=QueueStats)
async def queue_stats(
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> QueueStats:
    """Счётчики очереди: ждёт / в разборе / среднее время."""
    raise NotImplementedError  # TODO(Кирилл)
