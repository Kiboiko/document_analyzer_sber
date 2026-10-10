"""Кабинет оператора за OIDC (TZ 1, 5). Без входа PII не отдаём.

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.enums import (
    DOC_TYPE_TITLES,
    EVENT_TYPE_TITLES,
    FIELD_TITLES,
    REQUIRED_DOCUMENTS,
    Country,
    CrossCheckStatus,
    DocStatus,
    EventType,
    FieldSource,
    Role,
)
from app.model import validate
from app.models import Application, CrossCheck, Document, Field
from app.routers.auth import current_user
from app.schemas import (
    ApplicationCard,
    ApplicationListItem,
    ApplicationListOut,
    CrossCheckOut,
    CurrentUser,
    DocumentOut,
    FieldOut,
    FieldPatch,
    FileOut,
    QueueStats,
    ReparseResult,
)
from app.services import history, queue

router = APIRouter(tags=["operator"])


def _field_out(field: Field) -> FieldOut:
    out = FieldOut.model_validate(field)
    out.title = FIELD_TITLES.get(field.name)
    return out


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        doc_type=document.doc_type,
        title=DOC_TYPE_TITLES.get(document.doc_type),
        status=document.status,
        error_reason=document.error_reason,
        created_at=document.created_at,
        updated_at=document.updated_at,
        files=[FileOut.model_validate(f) for f in document.files],
        fields=[_field_out(f) for f in document.fields],
    )


async def require_operator(request: Request) -> CurrentUser:
    """Зависимость: пускать только с ролью operator или admin."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "требуется вход")
    if not (user.has_role(Role.OPERATOR.value) or user.has_role(Role.ADMIN.value)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "требуется роль operator")
    return user


# --------------------------------------------------------------------------- #
# Страницы кабинета
# --------------------------------------------------------------------------- #


@router.get("/operator", response_class=HTMLResponse)
async def page_applications(
    request: Request,
    country: Country | None = None,
    status: DocStatus | None = None,
    q: str | None = None,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Список заявок с фильтрами (страна, статус, поиск)."""
    data = await list_applications(
        country=country, status=status, q=q, user=user, session=session
    )
    return request.app.state.templates.TemplateResponse(
        request, "operator/applications.html", {"user": user, "data": data}
    )


@router.get("/operator/applications/{application_id}", response_class=HTMLResponse)
async def page_application_card(
    request: Request,
    application_id: uuid.UUID,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Карточка: оригинал рядом с полями, замечания, таймлайн истории."""
    card = await get_application_card(
        application_id=application_id, user=user, session=session
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "operator/card.html",
        {"user": user, "card": card, "event_titles": EVENT_TYPE_TITLES},
    )


@router.get("/operator/queue", response_class=HTMLResponse)
async def page_queue(
    request: Request,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Раздел «в работе»: что ждёт, что разбирается, среднее время."""
    stats = await queue_stats(user=user, session=session)
    return request.app.state.templates.TemplateResponse(
        request, "operator/queue.html", {"user": user, "stats": stats}
    )


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
    base_stmt = select(Application)
    if country is not None:
        base_stmt = base_stmt.where(Application.country == country)
    if status is not None:
        base_stmt = base_stmt.where(
            select(Document.id)
            .where(Document.application_id == Application.id, Document.status == status)
            .exists()
        )
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        base_stmt = base_stmt.where(
            select(Field.id)
            .join(Document, Document.id == Field.document_id)
            .where(
                Document.application_id == Application.id,
                Field.name == "full_name",
                Field.value.ilike(pattern),
            )
            .exists()
        )

    total = await session.scalar(
        select(func.count()).select_from(base_stmt.with_only_columns(Application.id).subquery())
    )

    stmt = (
        base_stmt.options(selectinload(Application.documents))
        .order_by(Application.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    applications = list((await session.scalars(stmt)).unique().all())

    mismatch_ids: set[uuid.UUID] = set()
    if applications:
        rows = await session.scalars(
            select(CrossCheck.application_id)
            .where(
                CrossCheck.application_id.in_([a.id for a in applications]),
                CrossCheck.status == CrossCheckStatus.MISMATCH,
            )
            .distinct()
        )
        mismatch_ids = set(rows.all())

    items = [
        ApplicationListItem(
            id=app_.id,
            country=app_.country,
            status=app_.status,
            created_at=app_.created_at,
            updated_at=app_.updated_at,
            documents_total=len(app_.documents),
            documents_done=sum(1 for d in app_.documents if d.status == DocStatus.DONE),
            documents_error=sum(1 for d in app_.documents if d.status == DocStatus.ERROR),
            documents_flagged=sum(
                1 for d in app_.documents if d.status == DocStatus.FLAGGED
            ),
            has_mismatch=app_.id in mismatch_ids,
        )
        for app_ in applications
    ]

    return ApplicationListOut(items=items, total=total or 0, limit=limit, offset=offset)


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
    app_ = await session.get(
        Application,
        application_id,
        options=[
            selectinload(Application.documents).selectinload(Document.files),
            selectinload(Application.documents).selectinload(Document.fields),
            selectinload(Application.cross_checks),
        ],
    )
    if app_ is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "заявка не найдена")

    required = REQUIRED_DOCUMENTS.get(app_.country, ())
    present_types = {d.doc_type for d in app_.documents}
    missing = [t for t in required if t not in present_types]

    events = await history.timeline(session, application_id)

    return ApplicationCard(
        id=app_.id,
        country=app_.country,
        status=app_.status,
        created_at=app_.created_at,
        updated_at=app_.updated_at,
        required_documents=list(required),
        missing_documents=missing,
        documents=[_document_out(d) for d in app_.documents],
        events=events,
        cross_checks=[CrossCheckOut.model_validate(c) for c in app_.cross_checks],
    )


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
    field = await session.get(Field, field_id)
    if field is None or field.document_id != document_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "поле не найдено")

    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "документ не найден")

    verdict = validate.validate_field(document.doc_type, field.name, payload.value)
    field.value = payload.value
    field.source = FieldSource.OPERATOR
    field.is_valid = verdict.is_valid
    field.validation_msg = verdict.message

    await history.record(
        session,
        application_id=document.application_id,
        type=EventType.FIELD_CORRECTED,
        actor=user.username,
        document_id=document_id,
        payload={"field": field.name, "value": payload.value},
    )
    await session.commit()

    return _field_out(field)


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
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "документ не найден")

    job = await queue.enqueue(session, document_id)
    document.status = DocStatus.ACCEPTED
    document.error_reason = None
    await history.record(
        session,
        application_id=document.application_id,
        type=EventType.REPARSE_REQUESTED,
        actor=user.username,
        document_id=document_id,
    )
    await session.commit()

    return ReparseResult(document_id=document_id, job_id=job.id, status=job.status)


@router.get("/api/operator/queue", response_model=QueueStats)
async def queue_stats(
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> QueueStats:
    """Счётчики очереди: ждёт / в разборе / среднее время."""
    return await queue.stats(session)
