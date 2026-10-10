"""Публичная часть: соискатель без входа, доступ по applicant_token (TZ 1, 5).

Доступ к заявке даёт только невычисляемый токен, и проверяется он на сервере
при каждом запросе. По чужому или выдуманному токену — 404, без намёка на то,
существует ли такая заявка.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.enums import (
    COUNTRY_TITLES,
    DOC_TYPE_TITLES,
    FIELD_TITLES,
    REQUIRED_DOCUMENTS,
    ApplicationStatus,
    Country,
    DocStatus,
    DocType,
    EventType,
)
from app.models import Application, Document
from app.schemas import (
    ApplicationCreate,
    ApplicationCreated,
    ApplicationStatusOut,
    DocumentOut,
    FieldOut,
    UploadResult,
)
from app.services import history, intake

log = logging.getLogger(__name__)
router = APIRouter(tags=["public"])


# --------------------------------------------------------------------------- #
# Доступ по токену
# --------------------------------------------------------------------------- #


def _not_found() -> HTTPException:
    """Один и тот же ответ на «нет заявки» и «не тот токен».

    Разные ответы подсказали бы, какие id существуют.
    """
    return HTTPException(status_code=404, detail="Заявка не найдена")


async def _load_by_token(
    session: AsyncSession,
    applicant_token: uuid.UUID,
    application_id: uuid.UUID | None = None,
) -> Application:
    """Заявка по токену (при необходимости — с проверкой id). Иначе 404.

    Сравнение через `compare_digest`: по времени ответа нельзя подбирать токен.
    """
    application = await session.scalar(
        select(Application)
        .where(Application.applicant_token == applicant_token)
        .options(
            selectinload(Application.documents).selectinload(Document.files),
            selectinload(Application.documents).selectinload(Document.fields),
        )
        .limit(1)
    )
    if application is None:
        raise _not_found()

    if application_id is not None and not secrets.compare_digest(
        str(application.id), str(application_id)
    ):
        raise _not_found()

    return application


# --------------------------------------------------------------------------- #
# Сборка представления
# --------------------------------------------------------------------------- #


def _document_out(document: Document) -> DocumentOut:
    """ORM-документ в DTO, с подписями для UI."""
    out = DocumentOut.model_validate(document)
    out.title = DOC_TYPE_TITLES.get(document.doc_type)
    out.fields = [
        FieldOut.model_validate(field).model_copy(
            update={"title": FIELD_TITLES.get(field.name, field.name)}
        )
        for field in document.fields
    ]
    return out


def _keep_polling(status: ApplicationStatusOut) -> bool:
    """Нужно ли фрагменту продолжать обновляться.

    Пока есть принятые или разбираемые документы — да. Когда всё досчитано,
    поллинг прекращаем: лишние запросы никому не нужны.
    """
    return any(
        doc.status in (DocStatus.ACCEPTED, DocStatus.PROCESSING)
        for doc in status.documents
    )


def _status_out(application: Application) -> ApplicationStatusOut:
    """Статус заявки целиком — то, что видит соискатель при поллинге."""
    required = list(REQUIRED_DOCUMENTS[Country(application.country)])
    documents = [_document_out(doc) for doc in application.documents]
    present = {doc.doc_type for doc in application.documents if doc.files}

    out = ApplicationStatusOut.model_validate(application)
    out.required_documents = required
    out.missing_documents = [t for t in required if t not in present]
    out.documents = documents
    return out


# --------------------------------------------------------------------------- #
# Страницы (Jinja + htmx)
# --------------------------------------------------------------------------- #


def _templates(request: Request):
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
async def page_index(request: Request) -> HTMLResponse:
    """Выбор страны и старт заявки."""
    return _templates(request).TemplateResponse(
        request,
        "public/index.html",
        {
            "countries": [(c, COUNTRY_TITLES[c]) for c in Country],
            "required": {
                c.value: [(t.value, DOC_TYPE_TITLES[t]) for t in REQUIRED_DOCUMENTS[c]]
                for c in Country
            },
        },
    )


@router.post("/start")
async def start_application(
    country: Country = Form(...),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Создать заявку из формы и увести соискателя на его страницу.

    Отдельная ручка для браузера без JS: API возвращает JSON, а здесь нужен
    редирект на ссылку с токеном.
    """
    application = await _create_application(session, country)
    return RedirectResponse(
        url=f"/a/{application.applicant_token}", status_code=303
    )


@router.get("/a/{applicant_token}", response_class=HTMLResponse)
async def page_application(
    request: Request,
    applicant_token: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Страница заявки: набор документов по стране, загрузка, статусы."""
    application = await _load_by_token(session, applicant_token)
    status = _status_out(application)

    return _templates(request).TemplateResponse(
        request,
        "public/application.html",
        {
            "app_id": application.id,
            "token": applicant_token,
            "country_title": COUNTRY_TITLES[Country(application.country)],
            "status": status,
            "doc_titles": {t.value: DOC_TYPE_TITLES[t] for t in DocType},
            "max_mb": settings.max_upload_mb,
            "max_files": settings.max_files_per_request,
            "error": request.query_params.get("error"),
            "upload_report": request.session.pop("upload_report", None),
            "keep_polling": _keep_polling(status),
        },
    )


@router.get("/a/{applicant_token}/status", response_class=HTMLResponse)
async def page_status_fragment(
    request: Request,
    applicant_token: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """htmx-фрагмент со статусами документов (поллинг, без ручного обновления)."""
    application = await _load_by_token(session, applicant_token)
    status = _status_out(application)

    return _templates(request).TemplateResponse(
        request,
        "public/_status.html",
        {
            "token": applicant_token,
            "status": status,
            "doc_titles": {t.value: DOC_TYPE_TITLES[t] for t in DocType},
            "keep_polling": _keep_polling(status),
        },
    )


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


@router.post("/api/applications", response_model=ApplicationCreated)
async def create_application(
    payload: ApplicationCreate,
    session: AsyncSession = Depends(get_session),
) -> ApplicationCreated:
    """Создать заявку, выдать applicant_token и обязательный набор документов."""
    application = await _create_application(session, payload.country)
    return ApplicationCreated(
        id=application.id,
        applicant_token=application.applicant_token,
        country=Country(application.country),
        required_documents=list(REQUIRED_DOCUMENTS[Country(application.country)]),
    )


@router.post(
    "/api/applications/{application_id}/documents",
    response_model=UploadResult,
)
async def upload_documents(
    application_id: uuid.UUID,
    applicant_token: uuid.UUID,
    doc_type: DocType = Form(...),
    files: list[UploadFile] = File(default_factory=list),
    session: AsyncSession = Depends(get_session),
) -> UploadResult:
    """Приём файлов: проверки на сервере, дедуп по sha256, enqueue, мгновенный ответ.

    Ждать разбор здесь нельзя — он идёт в фоне (TZ 6).
    """
    await _load_by_token(session, applicant_token, application_id)

    try:
        return await intake.accept_files(
            session,
            application_id,
            doc_type,
            list(files),
            actor=history.ACTOR_APPLICANT,
        )
    except intake.IntakeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/a/{applicant_token}/upload")
async def upload_from_form(
    request: Request,
    applicant_token: uuid.UUID,
    doc_type: DocType = Form(...),
    files: list[UploadFile] = File(default_factory=list),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Загрузка из формы страницы соискателя (браузер без JS).

    Итог приёма (что принято, что уже было, что отклонено и почему) кладём в
    подписанную сессию: так его нельзя подделать ссылкой с параметром.
    """
    application = await _load_by_token(session, applicant_token)

    try:
        result = await intake.accept_files(
            session,
            application.id,
            doc_type,
            list(files),
            actor=history.ACTOR_APPLICANT,
        )
    except intake.IntakeError as exc:
        log.warning("приём отклонён: %s", exc)
        return RedirectResponse(
            url=f"/a/{applicant_token}?error={quote(str(exc))}", status_code=303
        )

    request.session["upload_report"] = {
        "doc_title": DOC_TYPE_TITLES[doc_type],
        "accepted": len(result.accepted),
        "duplicates": [f.orig_name for f in result.duplicates],
        "rejected": [{"name": r.orig_name, "reason": r.reason} for r in result.rejected],
    }
    return RedirectResponse(url=f"/a/{applicant_token}", status_code=303)


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
    application = await _load_by_token(session, applicant_token, application_id)
    return _status_out(application)


# --------------------------------------------------------------------------- #
# Внутреннее
# --------------------------------------------------------------------------- #


async def _create_application(
    session: AsyncSession, country: Country
) -> Application:
    """Создать заявку и записать событие одной транзакцией."""
    application = Application(country=country, status=ApplicationStatus.CREATED)
    session.add(application)
    await session.flush()

    await history.record(
        session,
        application.id,
        EventType.APPLICATION_CREATED,
        history.ACTOR_APPLICANT,
        payload={"country": country.value},
    )
    await session.commit()
    log.info("создана заявка %s (%s)", application.id, country.value)
    return application
