"""Приём файла: проверки на сервере, дедуп, запись в хранилище, enqueue (TZ 5, 6).

Владелец: Степан (ветка feat/model-intake).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import uuid

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import DocType
from app.models import Document, File
from app.schemas import UploadResult


def sha256_of(data: bytes) -> str:
    """Хеш содержимого — ключ дедупликации вместе с document_id."""
    raise NotImplementedError  # TODO(Степан)


def check_file(filename: str, mime: str, size: int) -> str | None:
    """Проверить тип/размер. Вернуть причину отказа или None.

    Все проверки — на сервере; клиентским ограничениям не доверяем.
    """
    raise NotImplementedError  # TODO(Степан): MAX_UPLOAD_MB, ALLOWED_MIME


def storage_path(application_id: uuid.UUID, document_id: uuid.UUID, sha256: str) -> str:
    """Путь внутри STORAGE_DIR. Файлы рядом с кодом не кладём."""
    raise NotImplementedError  # TODO(Степан)


async def get_or_create_document(
    session: AsyncSession,
    application_id: uuid.UUID,
    doc_type: DocType,
) -> Document:
    """Документ нужного типа в заявке: один на тип, дозагрузка — в него же."""
    raise NotImplementedError  # TODO(Степан)


async def save_file(
    session: AsyncSession,
    document: Document,
    upload: UploadFile,
    data: bytes,
) -> File:
    """Записать файл в хранилище и строку File в БД."""
    raise NotImplementedError  # TODO(Степан)


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
    raise NotImplementedError  # TODO(Степан): queue.enqueue + history.record
