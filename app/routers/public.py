"""Публичная часть: соискатель без входа, доступ по applicant_token (TZ 1, 5).

Владелец: Степан (ветка feat/model-intake).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.enums import DocType
from app.schemas import (
    ApplicationCreate,
    ApplicationCreated,
    ApplicationStatusOut,
    UploadResult,
)

router = APIRouter(tags=["public"])


# --------------------------------------------------------------------------- #
# Страницы (Jinja + htmx)
# --------------------------------------------------------------------------- #


@router.get("/", response_class=HTMLResponse)
async def page_index(request: Request) -> HTMLResponse:
    """Выбор страны и старт заявки."""
    raise NotImplementedError  # TODO(Степан): templates/public/index.html


@router.get("/a/{applicant_token}", response_class=HTMLResponse)
async def page_application(
    request: Request,
    applicant_token: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Страница заявки: набор документов по стране, загрузка, статусы."""
    raise NotImplementedError  # TODO(Степан): templates/public/application.html


@router.get("/a/{applicant_token}/status", response_class=HTMLResponse)
async def page_status_fragment(
    request: Request,
    applicant_token: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """htmx-фрагмент со статусами документов (поллинг, без ручного обновления)."""
    raise NotImplementedError  # TODO(Степан)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@router.post("/api/applications", response_model=ApplicationCreated)
async def create_application(
    payload: ApplicationCreate,
    session: AsyncSession = Depends(get_session),
) -> ApplicationCreated:
    """Создать заявку, выдать applicant_token и обязательный набор документов."""
    raise NotImplementedError  # TODO(Степан): + Event(application_created)


@router.post(
    "/api/applications/{application_id}/documents",
    response_model=UploadResult,
)
async def upload_documents(
    application_id: uuid.UUID,
    applicant_token: uuid.UUID,
    doc_type: DocType,
    files: list[UploadFile],
    session: AsyncSession = Depends(get_session),
) -> UploadResult:
    """Приём файлов: проверки на сервере, дедуп по sha256, enqueue, мгновенный ответ.

    Ждать разбор здесь нельзя — он идёт в фоне (TZ 6).
    """
    raise NotImplementedError  # TODO(Степан): services.intake.accept_files


@router.get(
    "/api/applications/{application_id}/status",
    response_model=ApplicationStatusOut,
)
async def application_status(
    application_id: uuid.UUID,
    applicant_token: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ApplicationStatusOut:
    """Статусы документов и полей заявки. Токен проверяется на сервере."""
    raise NotImplementedError  # TODO(Степан)
