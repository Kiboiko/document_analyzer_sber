"""Очередь задач в Postgres: SKIP LOCKED + реапер зависших (TZ 6).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import JobStatus
from app.models import Job
from app.schemas import QueueStats


async def enqueue(session: AsyncSession, document_id: uuid.UUID) -> Job:
    """Поставить задачу разбора документа в очередь (status=pending).

    Идемпотентно: если по документу уже висит невзятая задача, возвращаем её.
    Иначе дозагрузка трёх файлов в один документ дала бы три разбора одного
    и того же, а olmOCR у нас один на команду.

    Коммит — за вызывающим: задача должна попасть в БД той же транзакцией,
    что и принятые файлы.

    NB(Кирилл): эту функцию добавил Степан, чтобы разблокировать приём
    (`services/intake.py`). Остальное в файле — твоё.
    """
    existing = await session.scalar(
        select(Job)
        .where(Job.document_id == document_id, Job.status == JobStatus.PENDING)
        .limit(1)
    )
    if existing is not None:
        return existing

    job = Job(document_id=document_id, status=JobStatus.PENDING)
    session.add(job)
    await session.flush()
    return job


async def claim_next(session: AsyncSession, worker_id: str) -> Job | None:
    """Взять одну задачу: SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1.

    Переводит в processing, ставит locked_at и worker_id в той же транзакции.
    """
    raise NotImplementedError  # TODO(Кирилл)


async def mark_done(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Задача выполнена: status=done, finished_at."""
    raise NotImplementedError  # TODO(Кирилл)


async def mark_failed(
    session: AsyncSession,
    job_id: uuid.UUID,
    error: str,
    retry: bool = True,
) -> None:
    """Ошибка провайдера/разбора: attempts++, retry до JOB_MAX_ATTEMPTS, иначе failed."""
    raise NotImplementedError  # TODO(Кирилл)


async def reap_stale(session: AsyncSession) -> int:
    """Вернуть зависшие processing в pending (attempts++). Вернуть число задач.

    Запускается на старте воркера и периодически — так работа не теряется
    при рестарте.
    """
    raise NotImplementedError  # TODO(Кирилл): JOB_LOCK_TIMEOUT_S


async def stats(session: AsyncSession) -> QueueStats:
    """Счётчики для раздела «в работе» и GET /api/operator/queue."""
    raise NotImplementedError  # TODO(Кирилл)
