"""Выдача оригинала загруженного файла оператору (карточка «оригинал рядом с полями»).

Контракт роута (его используют шаблоны кабинета):

    GET /api/operator/applications/{application_id}/documents/{document_id}/files/{file_id}

    200  байты файла; Content-Type = File.mime (определён по содержимому при
         приёме); Content-Disposition: inline, имя файла из File.orig_name
    401/403  нет сессии оператора (решает `require_operator`, зона Кирилла)
    404  файла нет, он принадлежит другому документу или другой заявке, либо
         лежит вне хранилища. Все случаи отвечают одинаково.

Ссылку на оригинал строит `original_url()`; в шаблонах — по готовым id из
`ApplicationCard` (заявка -> документ -> файл).

Доступ только для оператора и админа: у соискателя в этой части доступа нет,
а PII в файле не должна выдаваться по прямой ссылке без входа.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Document, File
from app.routers.operator import require_operator
from app.schemas import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(tags=["files"])

#: Управляющие символы, кавычки и разделители пути в имени файла от клиента.
#: Имя попадает в заголовок Content-Disposition, поэтому CR/LF и кавычки
#: недопустимы: они позволили бы подмешать лишние заголовки.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE_NAME_RE = re.compile(r'["\\/]')


def original_url(
    application_id: uuid.UUID, document_id: uuid.UUID, file_id: uuid.UUID
) -> str:
    """Ссылка на оригинал для карточки оператора."""
    return (
        f"/api/operator/applications/{application_id}"
        f"/documents/{document_id}/files/{file_id}"
    )


def _not_found() -> HTTPException:
    # Один и тот же ответ на все причины: по нему нельзя понять, существует ли
    # файл в другой заявке.
    return HTTPException(status_code=404, detail="Файл не найден")


def _safe_filename(name: str) -> str:
    # Управляющие символы удаляем (имя из одних CR/LF не должно стать «__»),
    # кавычки и слеши заменяем.
    cleaned = _UNSAFE_NAME_RE.sub("_", _CONTROL_RE.sub("", name)).strip()
    return cleaned or "document"


def _resolve_inside_storage(stored_path: str) -> Path | None:
    """Абсолютный путь к файлу, если он лежит внутри хранилища, иначе None.

    `stored_path` строится приложением из UUID и хеша, но это значение из БД:
    при правке строки руками (или будущей ошибке) в нём может оказаться `../`
    или абсолютный путь. Выдавать что-либо вне STORAGE_DIR нельзя.
    """
    root = settings.storage_dir.resolve()
    try:
        path = (root / stored_path).resolve()
    except (OSError, ValueError):
        return None
    if root not in path.parents:
        return None
    return path


@router.get(
    "/api/operator/applications/{application_id}"
    "/documents/{document_id}/files/{file_id}",
    response_class=FileResponse,
    responses={404: {"description": "Файл не найден"}},
)
async def get_original(
    application_id: uuid.UUID,
    document_id: uuid.UUID,
    file_id: uuid.UUID,
    user: CurrentUser = Depends(require_operator),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Оригинал файла: байты с проверкой цепочки заявка -> документ -> файл."""
    # Одним запросом: файл найдётся, только если он принадлежит этому документу,
    # а документ — этой заявке. Подмена любого из трёх id даёт 404.
    record = await session.scalar(
        select(File)
        .join(Document, File.document_id == Document.id)
        .where(
            File.id == file_id,
            File.document_id == document_id,
            Document.application_id == application_id,
        )
    )
    if record is None:
        raise _not_found()

    path = _resolve_inside_storage(record.stored_path)
    if path is None:
        log.error("файл %s: stored_path вне хранилища: %r", file_id, record.stored_path)
        raise _not_found()
    if not path.is_file():
        log.error("файл %s есть в БД, но не найден на диске: %s", file_id, path)
        raise _not_found()

    # Просмотр PII оператором: в таймлайн заявки не пишем (ТЗ 9 перечисляет
    # события исчерпывающе), но в журнале сервера след остаётся.
    log.info(
        "оператор %s открыл оригинал: заявка=%s документ=%s файл=%s",
        user.username, application_id, document_id, file_id,
    )

    return FileResponse(
        path,
        media_type=record.mime,
        filename=_safe_filename(record.orig_name),
        content_disposition_type="inline",
        headers={
            # Оригинал — персональные данные: не кэшировать ни в браузере на
            # диске, ни в промежуточных прокси.
            "Cache-Control": "private, no-store",
            # Тип уже проверен по содержимому при приёме; запрещаем браузеру
            # «угадывать» другой.
            "X-Content-Type-Options": "nosniff",
        },
    )
