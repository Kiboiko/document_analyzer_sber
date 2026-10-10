"""Local-LLM: Ollama (OLLAMA_HOST, LOCAL_VLM_MODEL) — вариант on-prem (TZ 7).

Переключение: MODEL_PROVIDER=local. Инструкция — docs/LOCAL_MODEL.md.
OCR в обоих вариантах один и тот же сервис (OCR_SERVICE_URL); локально заменяется
только LLM — на вход так же приходит текст, а не картинка.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.model.providers import (
    SERVICE_DOWN_MESSAGE,
    LLMResult,
    ProviderError,
    describe_http_error,
    parse_json_object,
    response_json,
)

log = logging.getLogger(__name__)

NAME = "local"
_MODEL_DOWN = SERVICE_DOWN_MESSAGE

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
    except httpx.HTTPError as exc:
        raise ProviderError(f"Ollama /api/tags: {describe_http_error(exc)}") from exc
    data = response_json(resp, "Ollama /api/tags")

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
        # Лимит на ответ: без него бредящая модель может писать до упора
        # контекста. Тот же бюджет, что и у cloud.
        "options": {"temperature": 0.0, "num_predict": settings.llm_max_tokens},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.model_timeout_s) as client:
            resp = await client.post(f"{_base_url()}/api/chat", json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"Ollama /api/chat: {describe_http_error(exc)}",
            retryable=True,
            user_message=_MODEL_DOWN,
        ) from exc
    try:
        data = response_json(resp, "Ollama /api/chat")
    except ProviderError as exc:
        raise ProviderError(
            str(exc), retryable=True, user_message=_MODEL_DOWN
        ) from exc

    if data.get("error"):
        # Ollama иногда отвечает 200 с текстом ошибки в теле.
        raise ProviderError(
            f"Ollama /api/chat: {str(data['error'])[:300]}",
            retryable=True,
            user_message=_MODEL_DOWN,
        )

    message = data.get("message")
    raw_content = message.get("content") if isinstance(message, dict) else None
    content = raw_content.strip() if isinstance(raw_content, str) else ""
    done_reason = data.get("done_reason")

    if not content:
        raise ProviderError(
            "Ollama вернул пустой content"
            + (f" (done_reason={done_reason})" if done_reason else "")
            + ": модель не дала ответа, нужен retry",
            retryable=True,
            user_message=_MODEL_DOWN,
        )

    if done_reason == "length":
        # Как и у cloud: обрезанный JSON может разобраться, но часть полей
        # потеряна, а неполный документ хуже честной ошибки.
        raise ProviderError(
            f"Ollama обрезал ответ по лимиту токенов (num_predict="
            f"{settings.llm_max_tokens}): нужен retry или больший LLM_MAX_TOKENS",
            retryable=True,
            user_message=_MODEL_DOWN,
        )

    try:
        fields = parse_json_object(content)
    except ProviderError as exc:
        raise ProviderError(
            str(exc), retryable=True, user_message=_MODEL_DOWN
        ) from exc

    return LLMResult(
        fields=fields,
        usage={
            "prompt_eval_count": data.get("prompt_eval_count"),
            "eval_count": data.get("eval_count"),
            "total_duration": data.get("total_duration"),
        },
        model=data.get("model", settings.local_vlm_model),
    )
