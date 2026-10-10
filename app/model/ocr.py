"""Клиент HTTP-сервиса OCR (OCR_SERVICE_URL) — шаг 1 разбора.

Сервис организаторов принимает файл как есть (PDF, PNG, JPEG, HEIC), сам
приводит его к PDF и возвращает распознанный текст документа в markdown:

    POST {OCR_SERVICE_URL}/ocr   multipart/form-data, поле "file"
    200  text/markdown — текст документа
    400  нечитаемый файл          401  неверный X-API-Token
    413  файл слишком большой     502  ошибка vLLM на стороне сервиса
    504  сервис не уложился в свой дедлайн (по умолчанию 240 с)

Ошибки отличаются тем, есть ли смысл повторять запрос и что сказать
соискателю, поэтому каждый код превращается в `OcrError` с этими признаками
(`retryable`, `user_message`), а решение о статусе документа остаётся за воркером.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.model.providers import ProviderError, describe_http_error

log = logging.getLogger(__name__)

__all__ = ["ocr_document", "sniff_mime", "OcrError"]


class OcrError(ProviderError):
    """OCR недоступен или не смог обработать файл."""


#: Что показать соискателю при сбое самого сервиса: в нём мы не виноваты и
#: он не должен гадать, что с его файлом.
_SERVICE_DOWN = "Сервис распознавания временно недоступен. Мы повторим попытку позже."

_EXT_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/tiff": ".tif",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

_HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx"})
_HEIF_BRANDS = frozenset({b"heif", b"mif1", b"msf1"})


def sniff_mime(data: bytes) -> str:
    """Определить тип по содержимому, а не по имени файла.

    Имя и Content-Type приходят от клиента, им доверять нельзя.
    """
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[4:8] == b"ftyp":
        brands = [data[8:12]] + [
            data[offset : offset + 4] for offset in range(16, len(data), 4)
        ]
        if any(brand in _HEIC_BRANDS for brand in brands):
            return "image/heic"
        if any(brand in _HEIF_BRANDS for brand in brands):
            return "image/heif"
    return "application/octet-stream"


def _service_url() -> str:
    return f"{settings.ocr_service_url.rstrip('/')}/ocr"


def _headers() -> dict[str, str]:
    # Токен нужен не везде: при открытом доступе заголовок не отправляем.
    # В журнал токен не пишем нигде.
    if settings.ocr_client_token:
        return {"X-API-Token": settings.ocr_client_token}
    return {}


def _detail(resp: httpx.Response) -> str:
    """Пояснение сервиса из тела ошибки: {"detail": "..."} либо просто текст."""
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])[:200]
    except ValueError:
        pass
    return (resp.text or "").strip().replace("\n", " ")[:200]


def _error_for_status(status: int, detail: str) -> OcrError:
    """Код ответа сервиса -> ошибка с признаками для воркера."""
    suffix = f": {detail}" if detail else ""

    if status == 400:
        # Сервис отвечает «not a readable PDF» даже на фото: он приводит всё
        # к PDF. Для соискателя это просто нечитаемый файл.
        return OcrError(
            f"OCR: файл не читается (HTTP 400){suffix}",
            retryable=False,
            user_message="Не удалось прочитать файл. Загрузите другое фото или скан.",
        )
    if status == 401:
        return OcrError(
            "OCR: сервис отклонил токен (HTTP 401), проверьте OCR_CLIENT_TOKEN",
            retryable=False,
            user_message=_SERVICE_DOWN,
        )
    if status == 413:
        return OcrError(
            f"OCR: файл слишком большой для сервиса (HTTP 413){suffix}",
            retryable=False,
            user_message="Файл слишком большой для распознавания. "
            "Загрузите файл меньшего размера.",
        )
    if status in (502, 504):
        kind = "ошибка модели" if status == 502 else "сервис не уложился во время"
        return OcrError(
            f"OCR: {kind} на стороне сервиса (HTTP {status}){suffix}",
            retryable=True,
            user_message=_SERVICE_DOWN,
        )
    if status >= 500:
        return OcrError(
            f"OCR: сбой сервиса (HTTP {status}){suffix}",
            retryable=True,
            user_message=_SERVICE_DOWN,
        )
    # Прочие 4xx (404 на неверный адрес, 405, 422...) — ошибка нашей настройки
    # или контракта; повтор не поможет, а соискатель ни при чём.
    return OcrError(
        f"OCR: неожиданный ответ сервиса (HTTP {status}){suffix}",
        retryable=False,
        user_message=_SERVICE_DOWN,
    )


async def ocr_document(data: bytes) -> str:
    """Распознать файл целиком и вернуть текст документа (markdown).

    Файл уходит как есть: конвертация и рендер страниц на стороне сервиса.
    Пустой текст — это ошибка, а не «пустой документ»: дальше он ушёл бы в
    LLM, и документ выглядел бы разобранным без единого поля.
    """
    if not data:
        raise OcrError(
            "OCR: пустой файл",
            retryable=False,
            user_message="Файл пустой. Загрузите другое фото или скан.",
        )

    mime = sniff_mime(data)
    filename = "document" + _EXT_BY_MIME.get(mime, "")

    try:
        # Таймаут с запасом над дедлайном самого сервиса (240 с): иначе мы
        # оборвали бы запрос раньше, чем он вернёт осмысленный 504.
        async with httpx.AsyncClient(timeout=settings.ocr_timeout_s) as client:
            resp = await client.post(
                _service_url(),
                headers=_headers(),
                files={"file": (filename, data, mime)},
            )
    except httpx.HTTPError as exc:
        raise OcrError(
            f"OCR недоступен: {describe_http_error(exc)}",
            retryable=True,
            user_message=_SERVICE_DOWN,
        ) from exc

    if resp.status_code != 200:
        raise _error_for_status(resp.status_code, _detail(resp))

    # Сырой ответ нужен, чтобы сверить разбор с тем, что реально отдаёт сервис.
    # В нём персональные данные, поэтому только DEBUG (settings.debug).
    log.debug("OCR raw response: %r", resp.text)

    text = (resp.text or "").strip()
    if not text:
        raise OcrError(
            "OCR вернул пустой текст: на изображении не нашли текста",
            retryable=False,
            user_message="На фото не удалось найти текст. "
            "Загрузите более чёткое фото документа.",
        )
    return text
