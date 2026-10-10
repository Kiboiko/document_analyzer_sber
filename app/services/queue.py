"""Очередь задач в Postgres: SKIP LOCKED + реапер зависших (TZ 6).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import JobStatus
from app.models import Job
from app.schemas import QueueStats


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(session: AsyncSession, document_id: uuid.UUID) -> Job:
    """Поставить задачу разбора документа в очередь (status=pending).

    Коммит — на вызывающей стороне (часть общей транзакции приёма/reparse).
    """
    job = Job(document_id=document_id, status=JobStatus.PENDING)
    session.add(job)
    await session.flush()
    return job


async def claim_next(session: AsyncSession, worker_id: str) -> Job | None:
    """Взять одну задачу: SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1.

    Переводит в processing, ставит locked_at и worker_id и сразу коммитит —
    статус должен быть виден другим воркерам/реаперу немедленно.
    """
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.PENDING)
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.scalars(stmt)).first()
    if job is None:
        await session.commit()
        return None

    job.status = JobStatus.PROCESSING
    job.locked_at = _utcnow()
    job.worker_id = worker_id
    job.started_at = _utcnow()
    await session.commit()
    return job


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
