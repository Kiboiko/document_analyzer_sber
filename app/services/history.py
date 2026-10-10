"""Журнал событий заявки: запись и чтение таймлайна (TZ 9).

Общий сервис: пишут обе ветки, поэтому меняем аккуратно.

Транзакции: `record` только добавляет событие в сессию и делает flush.
Коммит — за вызывающим, чтобы событие и изменение состояния попадали в БД
одной транзакцией. Иначе в истории появились бы записи о том, чего не было.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EventType
from app.models import Event
from app.schemas import EventOut

#: Значения `Event.actor` для неперсональных источников.
ACTOR_APPLICANT = "applicant"
ACTOR_WORKER = "worker"
ACTOR_SYSTEM = "system"


async def record(
    session: AsyncSession,
    application_id: uuid.UUID,
    type: EventType,
    actor: str,
    document_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Записать событие. Каждое значимое действие обязано пройти здесь."""
    event = Event(
        application_id=application_id,
        document_id=document_id,
        type=type,
        actor=actor,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    return event


async def timeline(
    session: AsyncSession,
    application_id: uuid.UUID,
    limit: int = 200,
) -> list[EventOut]:
    """События заявки по возрастанию времени — для карточки оператора."""
    stmt = (
        select(Event)
        .where(Event.application_id == application_id)
        .order_by(Event.created_at.asc(), Event.id.asc())
        .limit(limit)
    )
    rows = (await session.scalars(stmt)).all()
    return [EventOut.model_validate(row) for row in rows]
