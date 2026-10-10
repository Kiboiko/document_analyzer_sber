"""Фоновый воркер: отдельный процесс, `python -m app.worker` (TZ 6).

Цикл: реапер на старте → забирать задачи SKIP LOCKED → `recognize` →
поля + валидация + события → done/error/flagged. Параллельность —
asyncio-семафор (WORKER_CONCURRENCY). Любая ошибка провайдера не роняет процесс.

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import dispose_engine, session_factory
from app.enums import EXTRACTED_FIELDS, DocStatus, EventType, FieldSource
from app.model import recognize
from app.model.validate import validate_document
from app.models import Document, Field, Job
from app.schemas import ExtractResult, ValidationReport
from app.services import crosscheck, history, queue

log = logging.getLogger(__name__)

_WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def worker_id() -> str:
    """Идентификатор процесса-воркера (пишется в Job.worker_id)."""
    return _WORKER_ID


def _apply_fields(document: Document, result: ExtractResult, report: ValidationReport) -> None:
    """Записать распознанные поля в документ: upsert по (document_id, name)."""
    existing = {f.name: f for f in document.fields}
    verdicts = {v.name: v for v in report.fields}
    for name in EXTRACTED_FIELDS.get(document.doc_type, ()):
        field = existing.get(name)
        if field is None:
            field = Field(document_id=document.id, name=name)
            document.fields.append(field)
        verdict = verdicts.get(name)
        field.value = result.fields.get(name)
        field.source = FieldSource.MODEL
        field.confidence = result.confidence.get(name)
        field.is_valid = verdict.is_valid if verdict else True
        field.validation_msg = verdict.message if verdict else None


async def _fail_document(
    session, job_id: uuid.UUID, document: Document, reason: str
) -> None:
    """Документ не разобран: error_reason, Event(parse_failed), Job в retry/failed."""
    document.status = DocStatus.ERROR
    document.error_reason = reason
    await history.record(
        session,
        document.application_id,
        EventType.PARSE_FAILED,
        "worker",
        document_id=document.id,
        payload={"error": reason},
    )
    await session.commit()
    await queue.mark_failed(session, job_id, reason)


async def process_job(job: Job) -> None:
    """Разобрать один документ в своей сессии БД.

    Шаги: parse_started → recognize → validate → Field/Event → статус документа →
    crosscheck по заявке. Исключение провайдера здесь = mark_failed с retry,
    а не падение воркера (TZ 6).
    """
    document_id = job.document_id
    job_id = job.id
    actor = f"worker:{job.worker_id or worker_id()}"

    async with session_factory() as session:
        document = await session.get(
            Document,
            document_id,
            options=[selectinload(Document.files), selectinload(Document.fields)],
        )
        if document is None:
            await queue.mark_failed(session, job_id, "документ не найден", retry=False)
            return

        await history.record(
            session, document.application_id, EventType.PARSE_STARTED, actor,
            document_id=document_id,
        )
        await session.commit()

        if not document.files:
            await _fail_document(session, job_id, document, "нет загруженных файлов")
            return

        file_path = settings.storage_dir / document.files[-1].stored_path
        try:
            image_bytes = file_path.read_bytes()
        except OSError as exc:
            await _fail_document(session, job_id, document, f"файл недоступен: {exc}")
            return

        try:
            result = await recognize(document.doc_type, image_bytes)
        except Exception as exc:  # провайдер/сеть — не валим воркер (TZ 6)
            log.warning("recognize failed for document %s: %s", document_id, exc)
            await _fail_document(session, job_id, document, f"ошибка модели: {exc}")
            return

        report = validate_document(document.doc_type, result.fields)
        _apply_fields(document, result, report)
        document.status = report.status
        document.error_reason = "; ".join(report.notes) if report.notes else None

        await history.record(
            session, document.application_id, EventType.PARSE_DONE, "worker",
            document_id=document_id, payload={"status": report.status.value},
        )
        if report.status == DocStatus.FLAGGED:
            await history.record(
                session, document.application_id, EventType.DOCUMENT_FLAGGED, "worker",
                document_id=document_id, payload={"notes": report.notes},
            )
        await session.commit()

        # Бонус TZ 8: падение сопоставления ФИО/дат не должно портить разбор документа.
        try:
            await crosscheck.run_for_application(session, document.application_id)
            await session.commit()
        except Exception:
            log.exception(
                "crosscheck failed for application %s", document.application_id
            )
            await session.rollback()

    async with session_factory() as job_session:
        await queue.mark_done(job_session, job_id)


async def reaper_loop(stop: asyncio.Event) -> None:
    """Периодически возвращать зависшие processing в pending."""
    interval = max(settings.job_lock_timeout_s / 2, settings.worker_poll_interval_s)
    while not stop.is_set():
        async with session_factory() as session:
            n = await queue.reap_stale(session)
            if n:
                log.info("реапер: возвращено в pending задач: %s", n)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def worker_loop(stop: asyncio.Event) -> None:
    """Опрашивать очередь и запускать разбор под семафором."""
    tasks: set[asyncio.Task] = set()

    async def _run(job: Job) -> None:
        try:
            await process_job(job)
        except Exception:
            log.exception("process_job упал для job %s", job.id)

    while not stop.is_set():
        async with session_factory() as session:
            job = await queue.claim_next(session, worker_id())

        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_s)
            except TimeoutError:
                pass
            continue

        task = asyncio.create_task(_run(job))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        if len(tasks) >= settings.worker_concurrency:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    """Точка входа: реапер на старте, затем циклы до сигнала остановки."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows: сигналы недоступны через event loop

    async with session_factory() as session:
        n = await queue.reap_stale(session)
    log.info("воркер %s запущен (реапер вернул %s задач)", worker_id(), n)

    try:
        await asyncio.gather(worker_loop(stop), reaper_loop(stop))
    finally:
        await dispose_engine()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(main())
