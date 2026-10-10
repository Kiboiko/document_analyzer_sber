"""Приём файла: проверки на сервере, дедуп, запись в хранилище, enqueue (TZ 5, 6).

Приём мгновенный: файл лёг в хранилище, задача встала в очередь, ответ ушёл.
Разбор соискателя не ждёт.

Ни одна проверка здесь не повторяет клиент, а заменяет его: имя, Content-Type
и размер приходят от браузера и подделываются тривиально.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import (
    REQUIRED_DOCUMENTS,
    ApplicationStatus,
    Country,
    DocStatus,
    DocType,
    EventType,
)
from app.model import ocr
from app.models import Application, Document, File
from app.schemas import FileOut, UploadRejected, UploadResult
from app.services import history, queue

log = logging.getLogger(__name__)

#: Что реально умеет читать OCR-слой. Сверяем с содержимым, а не с заголовком.
ALLOWED_MIME = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/tiff", "application/pdf"}
)


class IntakeError(ValueError):
    """Приём невозможен целиком (не тот тип документа, нет заявки)."""


def sha256_of(data: bytes) -> str:
    """Хеш содержимого — ключ дедупликации вместе с document_id."""
    return hashlib.sha256(data).hexdigest()


def check_file(filename: str, mime: str, size: int) -> str | None:
    """Проверить тип/размер. Вернуть причину отказа или None.

    `mime` ожидается уже определённым по содержимому (`ocr.sniff_mime`).
    """
    if size <= 0:
        return "файл пустой"
    if size > settings.max_upload_bytes:
        return (
            f"файл больше {settings.max_upload_mb} МБ "
            f"({size / 1024 / 1024:.1f} МБ)"
        )
    if mime not in ALLOWED_MIME:
        return "неподдерживаемый формат: нужны JPEG, PNG, WEBP, TIFF или PDF"
    return None


#: Сколько символов хеша берём в имя файла. Полный sha256 лежит в БД и там же
#: обеспечивает дедуп, а в пути он только раздувает длину: два UUID плюс 64
#: символа упираются в лимит пути Windows (260) при разработке не в корне диска.
_NAME_HASH_LEN = 16


def storage_path(application_id: uuid.UUID, document_id: uuid.UUID, sha256: str) -> str:
    """Путь внутри STORAGE_DIR. Файлы рядом с кодом не кладём.

    Имя строим из хеша: имя от соискателя в путь не попадает, поэтому
    `../../etc/passwd` и прочая экзотика в имени безопасны.
    """
    return f"{application_id}/{document_id}/{sha256[:_NAME_HASH_LEN]}"


_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tif",
    "application/pdf": ".pdf",
}


async def get_or_create_document(
    session: AsyncSession,
    application_id: uuid.UUID,
    doc_type: DocType,
) -> Document:
    """Документ нужного типа в заявке: один на тип, дозагрузка — в него же."""
    existing = await session.scalar(
        select(Document)
        .where(
            Document.application_id == application_id,
            Document.doc_type == doc_type,
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    document = Document(
        application_id=application_id,
        doc_type=doc_type,
        status=DocStatus.ACCEPTED,
    )
    session.add(document)
    await session.flush()
    return document


async def save_file(
    session: AsyncSession,
    document: Document,
    upload: UploadFile,
    data: bytes,
) -> File:
    """Записать файл в хранилище и строку File в БД."""
    digest = sha256_of(data)
    mime = ocr.sniff_mime(data)
    rel_path = storage_path(document.application_id, document.id, digest)
    rel_path += _EXT_BY_MIME.get(mime, "")

    target = settings.storage_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    record = File(
        document_id=document.id,
        stored_path=rel_path,
        orig_name=(upload.filename or "без имени")[:255],
        sha256=digest,
        mime=mime,
        size=len(data),
    )
    session.add(record)
    await session.flush()
    return record


async def accept_files(
    session: AsyncSession,
    application_id: uuid.UUID,
    doc_type: DocType,
    uploads: list[UploadFile],
    actor: str,
) -> UploadResult:
    """Сквозной приём: проверки → дедуп → запись → Job(pending) → события.

    Возврат мгновенный: соискателя ожиданием разбора не блокируем.
    """
    application = await session.get(Application, application_id)
    if application is None:
        raise IntakeError("заявка не найдена")

    _check_doc_type_allowed(application.country, doc_type)

    if not uploads:
        raise IntakeError("не выбрано ни одного файла")
    if len(uploads) > settings.max_files_per_request:
        raise IntakeError(
            f"за один раз можно загрузить не больше "
            f"{settings.max_files_per_request} файлов"
        )

    document = await get_or_create_document(session, application_id, doc_type)
    known = await _known_hashes(session, document.id)

    accepted: list[FileOut] = []
    duplicates: list[FileOut] = []
    rejected: list[UploadRejected] = []

    await history.record(
        session,
        application_id,
        EventType.FILES_UPLOADED,
        actor,
        document_id=document.id,
        payload={"doc_type": doc_type.value, "files": len(uploads)},
    )

    for upload in uploads:
        name = upload.filename or "без имени"
        data, overflow = await _read_capped(upload, settings.max_upload_bytes)
        await upload.close()

        if overflow:
            reason = f"файл больше {settings.max_upload_mb} МБ"
            rejected.append(UploadRejected(orig_name=name, reason=reason))
            await history.record(
                session,
                application_id,
                EventType.FILE_REJECTED,
                actor,
                document_id=document.id,
                payload={"orig_name": name, "reason": reason},
            )
            continue

        # Тип определяем по содержимому: Content-Type от клиента — подсказка,
        # а не факт.
        mime = ocr.sniff_mime(data)
        reason = check_file(name, mime, len(data))
        if reason:
            rejected.append(UploadRejected(orig_name=name, reason=reason))
            await history.record(
                session,
                application_id,
                EventType.FILE_REJECTED,
                actor,
                document_id=document.id,
                payload={"orig_name": name, "reason": reason, "size": len(data)},
            )
            continue

        digest = sha256_of(data)
        if digest in known:
            duplicates.append(FileOut.model_validate(known[digest]))
            continue

        record = await save_file(session, document, upload, data)
        known[digest] = record
        accepted.append(FileOut.model_validate(record))
        await history.record(
            session,
            application_id,
            EventType.FILE_ACCEPTED,
            actor,
            document_id=document.id,
            payload={
                "orig_name": record.orig_name,
                "sha256": digest,
                "mime": record.mime,
                "size": record.size,
            },
        )

    if accepted:
        # Разбор ставим один на документ, даже если принято несколько файлов.
        document.status = DocStatus.ACCEPTED
        document.error_reason = None
        await queue.enqueue(session, document.id)

    await refresh_application_status(session, application)
    await session.commit()
    log.info(
        "приём: заявка=%s тип=%s принято=%d дублей=%d отказов=%d",
        application_id, doc_type.value, len(accepted), len(duplicates), len(rejected),
    )

    return UploadResult(
        document_id=document.id,
        doc_type=doc_type,
        status=document.status,
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
    )


async def refresh_application_status(
    session: AsyncSession, application: Application
) -> ApplicationStatus:
    """Пересчитать агрегированный статус заявки по статусам её документов.

    Статус заявки — производная величина, и держать её в БД нужно только ради
    фильтров в кабинете. Поэтому у неё один пересчёт на всех, а не две копии
    логики: вызывается из приёма и должен вызываться из воркера после разбора.

    NB(Кирилл): позови это в `worker.process_job` после выставления статуса
    документа, иначе фильтр по статусу заявки в кабинете будет отставать.
    Коммит — за вызывающим.
    """
    statuses = (
        await session.execute(
            select(Document.doc_type, Document.status).where(
                Document.application_id == application.id
            )
        )
    ).all()

    if not statuses:
        status = ApplicationStatus.CREATED
    else:
        required = set(REQUIRED_DOCUMENTS[Country(application.country)])
        settled = {
            doc_type
            for doc_type, doc_status in statuses
            if doc_status in (DocStatus.DONE, DocStatus.FLAGGED)
        }
        # «Готова» — когда по каждому обязательному типу есть разобранный
        # документ. Error не считается: по нему ещё ждём нормальное фото.
        status = (
            ApplicationStatus.DONE
            if required <= settled
            else ApplicationStatus.PROCESSING
        )

    application.status = status
    return status


def _check_doc_type_allowed(country: Country, doc_type: DocType) -> None:
    """Тип документа должен входить в набор страны заявки (TZ 2).

    Иначе подменой поля формы можно было бы завести в российскую заявку
    узбекский патент.
    """
    allowed = REQUIRED_DOCUMENTS[Country(country)]
    if doc_type not in allowed:
        raise IntakeError(
            f"тип документа {doc_type.value} не входит в набор для выбранной страны"
        )


#: Размер куска при чтении загрузки.
_CHUNK = 1024 * 1024


async def _read_capped(upload: UploadFile, limit: int) -> tuple[bytes, bool]:
    """Прочитать загрузку не больше `limit` байт.

    Читаем кусками и останавливаемся на первом байте за лимитом: иначе
    заявленный размер пришлось бы узнавать уже после того, как мы приняли
    в память сколько угодно данных.
    """
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return b"", True
        chunks.append(chunk)

    return b"".join(chunks), False


async def _known_hashes(
    session: AsyncSession, document_id: uuid.UUID
) -> dict[str, File]:
    """Уже загруженные файлы документа по sha256 — для дедупа."""
    rows = await session.scalars(
        select(File).where(File.document_id == document_id)
    )
    return {row.sha256: row for row in rows}
