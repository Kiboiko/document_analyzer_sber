"""Local-LLM/VLM: Ollama (OLLAMA_HOST, LOCAL_VLM_MODEL) — вариант on-prem (TZ 7).

Переключение: MODEL_PROVIDER=local. Инструкция — docs/LOCAL_MODEL.md.
Владелец: Степан. Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

NAME = "local"


async def list_models() -> list[str]:
    """Что реально загружено в Ollama."""
    raise NotImplementedError  # TODO(Степан)


async def complete_json(prompt: str, text: str) -> dict:
    """Извлечь поля в JSON локальной моделью."""
    raise NotImplementedError  # TODO(Степан)
