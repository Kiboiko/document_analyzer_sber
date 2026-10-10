"""Клиент self-hosted olmOCR на vLLM (OLMOCR_SERVER) — шаг 1 разбора.

Владелец: Степан. Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

__all__ = ["to_png_pages", "ocr_text"]


def to_png_pages(data: bytes, mime: str) -> list[bytes]:
    """Привести вход (изображение или PDF) к списку PNG-страниц."""
    raise NotImplementedError  # TODO(Степан)


async def ocr_text(image_bytes: bytes) -> str:
    """Текст страницы от olmOCR. Адрес и модель — из env."""
    raise NotImplementedError  # TODO(Степан): OLMOCR_SERVER, OLMOCR_MODEL
