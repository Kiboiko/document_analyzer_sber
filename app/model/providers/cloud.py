"""Cloud-LLM: Qwen через внешний API ilons (LLM_BASE_URL, ILONS_API_KEY).

Владелец: Степан. Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

NAME = "cloud"


async def list_models() -> list[str]:
    """GET /v1/models — сверить фактическое имя модели с LLM_MODEL."""
    raise NotImplementedError  # TODO(Степан)


async def complete_json(prompt: str, text: str) -> dict:
    """Извлечь поля в JSON по промпту и OCR-тексту."""
    raise NotImplementedError  # TODO(Степан)
