"""Очередь задач в Postgres: SKIP LOCKED + реапер зависших (TZ 6).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import DocStatus, JobStatus
from app.models import Document, Job
from app.schemas import QueueStats


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(session: AsyncSession, document_id: uuid.UUID) -> Job:
    """Поставить задачу разбора документа в очередь (status=pending).

    Идемпотентно: если по документу уже висит невзятая задача, возвращаем её.
    Иначе дозагрузка трёх файлов в один документ дала бы три разбора одного
    и того же, а olmOCR у нас один на команду.

    Коммит — за вызывающим: задача должна попасть в БД той же транзакцией,
    что и принятые файлы (приём) или сбросом статуса документа (reparse).
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


async def _back_to_queue(session: AsyncSession, jobs: list[Job]) -> None:
    """Задачи — в pending, их документы — снова «в очереди», а не «разбирается»."""
    for job in jobs:
        job.status = JobStatus.PENDING
        job.locked_at = None
        job.worker_id = None
    if jobs:
        await session.execute(
            update(Document)
            .where(
                Document.id.in_([j.document_id for j in jobs]),
                Document.status == DocStatus.PROCESSING,
            )
            .values(status=DocStatus.ACCEPTED)
        )


async def reap_stale(session: AsyncSession, own_worker_id: str | None = None) -> int:
    """Вернуть зависшие processing в pending (attempts++). Вернуть число задач.

    Зависшая — та, чей воркер перестал продлевать блокировку (`heartbeat`)
    дольше JOB_LOCK_TIMEOUT_S. `own_worker_id` — на старте воркера: задачи с
    тем же id остались от его прошлого запуска в этом же контейнере (hostname и
    pid совпадают), их владелец точно мёртв — забираем сразу, без таймаута.
    """
    threshold = _utcnow() - timedelta(seconds=settings.job_lock_timeout_s)
    stale = Job.locked_at < threshold
    if own_worker_id:
        stale = or_(stale, Job.worker_id == own_worker_id)
    stmt = (
        select(Job)
        .where(Job.status == JobStatus.PROCESSING, stale)
        .with_for_update(skip_locked=True)
    )
    jobs = list((await session.scalars(stmt)).all())
    for job in jobs:
        job.attempts += 1
    await _back_to_queue(session, jobs)
    await session.commit()
    return len(jobs)


async def heartbeat(session: AsyncSession, job_ids: list[uuid.UUID]) -> None:
    """Продлить блокировку задач, которые воркер сейчас разбирает."""
    if not job_ids:
        return
    await session.execute(
        update(Job)
        .where(Job.id.in_(job_ids), Job.status == JobStatus.PROCESSING)
        .values(locked_at=_utcnow())
    )
    await session.commit()


async def release(session: AsyncSession, job_ids: list[uuid.UUID]) -> int:
    """Вернуть свои незаконченные задачи в очередь при штатной остановке.

    Попытку не списываем: разбор прервали мы, а не сбой модели.
    """
    if not job_ids:
        return 0
    jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.id.in_(job_ids), Job.status == JobStatus.PROCESSING
                )
            )
        ).all()
    )
    await _back_to_queue(session, jobs)
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
