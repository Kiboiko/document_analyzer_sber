"""Local-LLM: Ollama (OLLAMA_HOST, LOCAL_VLM_MODEL) — вариант on-prem (TZ 7).

Переключение: MODEL_PROVIDER=local. Инструкция — docs/LOCAL_MODEL.md.
OCR в обоих вариантах остаётся self-hosted (olmOCR); локально заменяется
только LLM — на вход так же приходит текст, а не картинка.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.model.providers import LLMResult, ProviderError, parse_json_object

log = logging.getLogger(__name__)

NAME = "local"

_SYSTEM = (
    "Ты извлекаешь поля из текста документа. "
    "Отвечай только JSON-объектом, без пояснений и без markdown. "
    "Если поля нет в тексте — верни для него null. Не придумывай значения."
)


def _base_url() -> str:
    return settings.ollama_host.rstrip("/")


async def list_models() -> list[str]:
    """Что реально загружено в Ollama (GET /api/tags)."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{_base_url()}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"Ollama /api/tags недоступен: {exc}") from exc

    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


async def complete_json(prompt: str, text: str) -> LLMResult:
    """Извлечь поля в JSON локальной моделью.

    `format: json` у Ollama заставляет модель отдать валидный JSON — для
    небольших локальных моделей это надёжнее, чем просить об этом в промпте.
    """
    payload = {
        "model": settings.local_vlm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"{prompt}\n\nТЕКСТ ДОКУМЕНТА:\n{text}"},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.0},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.model_timeout_s) as client:
            resp = await client.post(f"{_base_url()}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise ProviderError(f"Ollama /api/chat: {exc}") from exc

    content = (data.get("message") or {}).get("content")
    if not content:
        raise ProviderError(f"неожиданная форма ответа Ollama: {data}")

    return LLMResult(
        fields=parse_json_object(content),
        usage={
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "total_duration": data.get("total_duration"),
        },
        model=data.get("model", settings.local_vlm_model),
    )
