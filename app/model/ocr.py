"""Клиент self-hosted olmOCR на vLLM (OLMOCR_SERVER) — шаг 1 разбора.

Вход может быть фото (соискатель снимает документ телефоном) или PDF.
Приводим всё к PNG-страницам и спрашиваем текст у olmOCR.
"""

from __future__ import annotations

import base64
import io
import logging

import httpx

from app.config import settings
from app.model.providers import ProviderError

log = logging.getLogger(__name__)

__all__ = ["to_png_pages", "ocr_text", "sniff_mime", "OcrError"]


class OcrError(ProviderError):
    """OCR недоступен или не смог обработать страницу."""


#: Промпт, на котором обучался olmOCR-2: просим «прочитать страницу как есть».
#: Модель отвечает YAML-front-matter + текст, front matter мы отрезаем.
_OLMOCR_PROMPT = (
    "Attached is one page of a document that you must process. "
    "Just return the plain text representation of this document as if you "
    "were reading it naturally. Convert equations to LateX and tables to "
    "markdown.\n"
    "Return your output as markdown, with a front matter section on top "
    "specifying values for the primary_language, is_rotation_valid, "
    "rotation_correction, is_table, and is_diagram parameters."
)

#: Больше 2000 px по длинной стороне модели не нужно, а трафик и время растут.
_MAX_SIDE = 2000
#: Разрешение рендера PDF: 200 dpi хватает, чтобы читались серия и номер.
_PDF_SCALE = 200 / 72
#: Сколько страниц разбираем максимум — защита от PDF на 300 листов.
_MAX_PAGES = 5


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
    return "application/octet-stream"


def to_png_pages(data: bytes, mime: str | None = None) -> list[bytes]:
    """Привести вход (изображение или PDF) к списку PNG-страниц.

    Битый ввод — это OcrError с понятным текстом, а не падение воркера.
    """
    if not data:
        raise OcrError("пустой файл")

    mime = mime or sniff_mime(data)

    if mime == "application/pdf":
        return _pdf_to_png(data)
    return [_image_to_png(data)]


def _image_to_png(data: bytes) -> bytes:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as img:
            # EXIF-поворот: фото с телефона часто лежит «на боку».
            from PIL import ImageOps

            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise OcrError(f"не удалось прочитать изображение: {exc}") from exc


def _pdf_to_png(data: bytes) -> list[bytes]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise OcrError("pypdfium2 не установлен: PDF не поддержан") from exc

    pages: list[bytes] = []
    try:
        doc = pdfium.PdfDocument(data)
    except Exception as exc:
        raise OcrError(f"не удалось открыть PDF: {exc}") from exc

    try:
        for index in range(min(len(doc), _MAX_PAGES)):
            page = doc[index]
            image = page.render(scale=_PDF_SCALE).to_pil()
            image.thumbnail((_MAX_SIDE, _MAX_SIDE))
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="PNG", optimize=True)
            pages.append(buf.getvalue())
    except Exception as exc:
        raise OcrError(f"не удалось отрендерить PDF: {exc}") from exc
    finally:
        doc.close()

    if not pages:
        raise OcrError("в PDF нет страниц")
    return pages


def _strip_front_matter(text: str) -> str:
    """Отрезать YAML-блок, которым olmOCR предваряет текст страницы."""
    stripped = text.strip()
    if not stripped.startswith("---"):
        return stripped

    parts = stripped.split("---", 2)
    # ["", "<yaml>", "<текст>"] — если закрывающего разделителя нет, не трогаем.
    if len(parts) == 3:
        return parts[2].strip()
    return stripped


async def ocr_text(image_bytes: bytes) -> str:
    """Текст страницы от olmOCR. Адрес и модель — из env."""
    if not settings.olmocr_server:
        raise OcrError("OLMOCR_SERVER не задан: OCR недоступен")

    base = settings.olmocr_server.rstrip("/")
    # В env адрес уже с /v1 — не дублируем суффикс, если его забыли.
    url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": settings.olmocr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _OLMOCR_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 3000,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.model_timeout_s) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise OcrError(f"olmOCR недоступен: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OcrError(f"неожиданная форма ответа olmOCR: {data}") from exc

    return _strip_front_matter(content or "")
