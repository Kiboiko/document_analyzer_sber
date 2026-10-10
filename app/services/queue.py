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
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.status = JobStatus.DONE
    job.finished_at = _utcnow()
    await session.commit()


async def mark_failed(
    session: AsyncSession,
    job_id: uuid.UUID,
    error: str,
    retry: bool = True,
) -> None:
    """Ошибка провайдера/разбора: attempts++, retry до JOB_MAX_ATTEMPTS, иначе failed."""
    job = await session.get(Job, job_id)
    if job is None:
        return
    job.attempts += 1
    job.last_error = error[:2000]
    if retry and job.attempts < settings.job_max_attempts:
        job.status = JobStatus.PENDING
        job.locked_at = None
        job.worker_id = None
    else:
        job.status = JobStatus.FAILED
        job.finished_at = _utcnow()
    await session.commit()


async def reap_stale(session: AsyncSession) -> int:
    """Вернуть зависшие processing в pending (attempts++). Вернуть число задач.

    Запускается на старте воркера и периодически — так работа не теряется
    при рестарте.
    """
    threshold = _utcnow() - timedelta(seconds=settings.job_lock_timeout_s)
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.PROCESSING, Job.locked_at < threshold)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.scalars(stmt)).all())
    for job in jobs:
        job.status = JobStatus.PENDING
        job.attempts += 1
        job.locked_at = None
        job.worker_id = None
    await session.commit()
    return len(jobs)


async def stats(session: AsyncSession) -> QueueStats:
    """Счётчики для раздела «в работе» и GET /api/operator/queue."""
    now = _utcnow()
    hour_ago = now - timedelta(hours=1)

    async def count(*where) -> int:
        result = await session.scalar(
            select(func.count()).select_from(Job).where(*where)
        )
        return result or 0

    pending = await count(Job.status == JobStatus.PENDING)
    processing = await count(Job.status == JobStatus.PROCESSING)
    failed = await count(Job.status == JobStatus.FAILED)
    done_last_hour = await count(
        Job.status == JobStatus.DONE, Job.finished_at >= hour_ago
    )

    avg_duration = await session.scalar(
        select(func.avg(func.extract("epoch", Job.finished_at - Job.started_at)))
        .select_from(Job)
        .where(Job.status == JobStatus.DONE, Job.finished_at >= hour_ago)
    )

    oldest_pending_created = await session.scalar(
        select(func.min(Job.created_at)).select_from(Job).where(
            Job.status == JobStatus.PENDING
        )
    )
    oldest_pending_age_s = (
        (now - oldest_pending_created).total_seconds()
        if oldest_pending_created is not None
        else None
    )

    return QueueStats(
        pending=pending,
        processing=processing,
        failed=failed,
        done_last_hour=done_last_hour,
        avg_duration_s=float(avg_duration) if avg_duration is not None else None,
        oldest_pending_age_s=oldest_pending_age_s,
    )
