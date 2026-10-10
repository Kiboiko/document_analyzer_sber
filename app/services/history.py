"""Журнал событий заявки: запись и чтение таймлайна (TZ 9).

Общий сервис: пишут обе ветки, поэтому меняем аккуратно.
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EventType
from app.models import Event
from app.schemas import EventOut


async def record(
    session: AsyncSession,
    application_id: uuid.UUID,
    type: EventType,
    actor: str,
    document_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Записать событие. Каждое значимое действие обязано пройти здесь."""
    raise NotImplementedError  # TODO


async def timeline(
    session: AsyncSession,
    application_id: uuid.UUID,
    limit: int = 200,
) -> list[EventOut]:
    """События заявки по возрастанию времени — для карточки оператора."""
    raise NotImplementedError  # TODO
