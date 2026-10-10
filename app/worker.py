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
from app.enums import DOC_TYPE_TITLES, EXTRACTED_FIELDS, DocStatus, EventType, FieldSource
from app.model import ProviderError, recognize
from app.model.validate import missing_reason, validate_document
from app.models import Application, Document, Field, Job
from app.schemas import ExtractResult, ValidationReport
from app.services import crosscheck, history, queue
from app.services.intake import refresh_application_status

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


_RETRY_LATER = "Распознавание временно не удалось. Мы повторим попытку автоматически."
_GIVE_UP = (
    "Не удалось распознать документ. Загрузите фото ещё раз "
    "или дождитесь, пока сотрудник запустит повторную проверку."
)
_BAD_FILE = "Не удалось распознать документ. Загрузите более чёткое фото или скан."


async def _fail_document(
    session,
    job: Job,
    document: Document,
    *,
    user_message: str | None,
    detail: str,
    retryable: bool,
) -> None:
    """Документ не разобран: Event(parse_failed), Job в retry/failed.

    `error_reason` видит соискатель, поэтому туда идёт только `user_message`;
    технический `detail` — в журнал событий, лог и Job.last_error.
    Пока будет повтор, документ остаётся в очереди, а не «не распознан».
    """
    will_retry = retryable and job.attempts + 1 < settings.job_max_attempts
    log.warning(
        "job %s: документ %s не разобран (повтор=%s): %s",
        job.id, document.id, will_retry, detail,
    )
    if will_retry:
        document.status = DocStatus.ACCEPTED
        document.error_reason = user_message or _RETRY_LATER
    else:
        document.status = DocStatus.ERROR
        document.error_reason = (
            _GIVE_UP if retryable else (user_message or _BAD_FILE)
        )
    await history.record(
        session,
        document.application_id,
        EventType.PARSE_FAILED,
        "worker",
        document_id=document.id,
        payload={"error": document.error_reason, "detail": detail, "retry": will_retry},
    )
    application = await session.get(Application, document.application_id)
    if application is not None:
        await refresh_application_status(session, application)
    await session.commit()
    await queue.mark_failed(session, job.id, detail, retry=will_retry)


#: Сколько последних файлов документа распознаём: каждый — отдельный вызов
#: OCR+LLM (~20 с), и разбор должен уложиться в JOB_LOCK_TIMEOUT_S.
_MAX_FILES_PER_DOCUMENT = 5


async def _recognize_files(document: Document) -> ExtractResult:
    """Распознать все файлы документа (лицо, оборот, повторное фото) и свести поля.

    Файлы одной загрузки имеют одинаковый created_at, так что «последний файл»
    случаен — распознаём каждый. Идём от новых к старым: значение из более
    свежего файла приоритетнее, пустые поля добираем из остальных.
    """
    files = list(reversed(document.files))[:_MAX_FILES_PER_DOCUMENT]
    results: list[ExtractResult] = []
    last_error: Exception | None = None
    for file in files:
        try:
            data = (settings.storage_dir / file.stored_path).read_bytes()
            results.append(await recognize(document.doc_type, data))
        except (OSError, ProviderError) as exc:
            log.warning("документ %s, файл %s: %s", document.id, file.id, exc)
            last_error = exc
    if not results:
        assert last_error is not None
        raise last_error

    fields: dict[str, str | None] = {}
    confidence: dict[str, float] = {}
    for res in results:
        for name, value in res.fields.items():
            if fields.get(name) in (None, "") and value not in (None, ""):
                fields[name] = value
                if name in res.confidence:
                    confidence[name] = res.confidence[name]
            fields.setdefault(name, value)

    return ExtractResult(
        fields=fields,
        raw_text="\n\n---\n\n".join(r.raw_text for r in results),
        provider=results[0].provider,
        usage={**results[0].usage, "files_recognized": len(results)},
        confidence=confidence,
    )


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

        reupload = "Файл документа не найден. Загрузите фото ещё раз."
        if not document.files:
            await _fail_document(
                session, job, document,
                user_message=reupload, detail="нет загруженных файлов", retryable=False,
            )
            return

        try:
            result = await _recognize_files(document)
        except OSError as exc:
            await _fail_document(
                session, job, document,
                user_message=reupload, detail=f"файл недоступен: {exc}", retryable=False,
            )
            return
        except ProviderError as exc:
            await _fail_document(
                session, job, document,
                user_message=exc.user_message,
                detail=str(exc) or type(exc).__name__,
                retryable=exc.retryable,
            )
            return
        except Exception as exc:  # непредвиденное — не валим воркер (TZ 6)
            # У части исключений (например NotImplementedError) текст пустой.
            await _fail_document(
                session, job, document,
                user_message=None,
                detail=f"{type(exc).__name__}: {exc}",
                retryable=True,
            )
            return

        report = validate_document(document.doc_type, result.fields)
        _apply_fields(document, result, report)
        document.status = report.status
        if report.status == DocStatus.ERROR:
            if not any(v not in (None, "") for v in result.fields.values()):
                # Ни одного поля: чаще всего загружен не тот документ
                # (промпты велят вернуть null для чужого документа).
                document.error_reason = (
                    f"На фото не нашлось данных документа «{DOC_TYPE_TITLES[document.doc_type]}». "
                    "Проверьте, что загружен нужный документ, и сфотографируйте его "
                    "целиком при хорошем свете."
                )
            else:
                document.error_reason = missing_reason(
                    document.doc_type, report.missing_required
                )
        else:
            document.error_reason = "; ".join(report.notes) if report.notes else None

        application = await session.get(Application, document.application_id)
        if application is not None:
            await refresh_application_status(session, application)

        await history.record(
            session, document.application_id, EventType.PARSE_DONE, "worker",
            document_id=document_id,
            payload={"status": report.status.value, "provider": result.provider},
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

        log.info(
            "job %s: документ %s разобран (%s)", job_id, document_id, report.status.value
        )

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
