"""Cloud-LLM: Qwen через внешний API ilons (LLM_BASE_URL, ILONS_API_KEY).

OpenAI-совместимый /v1/chat/completions. Адрес, ключ и имя модели — из env.
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

NAME = "cloud"
_MODEL_DOWN = SERVICE_DOWN_MESSAGE

#: Системная инструкция: роль модели — извлечь, а не рассуждать.
_SYSTEM = (
    "Ты извлекаешь поля из текста документа. "
    "Отвечай только JSON-объектом, без пояснений и без markdown. "
    "Если поля нет в тексте — верни для него null. Не придумывай значения."
)


def _base_url() -> str:
    return settings.llm_base_url.rstrip("/")


def _headers() -> dict[str, str]:
    if not settings.ilons_api_key:
        # Наша настройка, а не вина соискателя. Повтор не поможет, пока ключ не
        # задан, поэтому retryable=False: пусть ошибка будет видна сразу, а
        # после правки .env документ можно разобрать заново.
        raise ProviderError(
            "ILONS_API_KEY не задан: cloud-провайдер недоступен",
            retryable=False,
            user_message=_MODEL_DOWN,
        )
    return {
        "Authorization": f"Bearer {settings.ilons_api_key}",
        "Content-Type": "application/json",
    }


async def list_models() -> list[str]:
    """GET /v1/models — сверить фактическое имя модели с LLM_MODEL.

    Имя из env легко разъезжается с тем, что реально отдаёт сервер,
    поэтому сверка вынесена в отдельный вызов (используется в диагностике).
    """
    url = f"{_base_url()}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_headers())
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ProviderError(f"ilons /v1/models: {describe_http_error(exc)}") from exc
    data = response_json(resp, "ilons /v1/models")

    return [item.get("id", "") for item in data.get("data", []) if item.get("id")]


async def complete_json(prompt: str, text: str) -> LLMResult:
    """Извлечь поля в JSON по промпту и OCR-тексту."""
    url = f"{_base_url()}/v1/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"{prompt}\n\nТЕКСТ ДОКУМЕНТА:\n{text}"},
        ],
        # Детерминированность важнее «красоты»: одинаковый вход — одинаковый выход.
        "temperature": 0.0,
        # Qwen у ilons — reasoning-модель: сначала она тратит токены на
        # рассуждения, и только потом пишет ответ. На тесном лимите бюджет
        # уходит в рассуждения целиком, а content приходит пустым.
        "max_tokens": settings.llm_max_tokens,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=settings.model_timeout_s) as client:
            resp = await client.post(url, headers=_headers(), json=payload)
            if resp.status_code == 400 and "response_format" in resp.text:
                # Не все сборки vLLM/шлюзы умеют json_object — повторяем без него.
                log.warning("ilons не принял response_format, повтор без него")
                payload.pop("response_format")
                resp = await client.post(url, headers=_headers(), json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"ilons chat/completions: {describe_http_error(exc)}",
            retryable=True,
            user_message=_MODEL_DOWN,
        ) from exc
    try:
        data = response_json(resp, "ilons chat/completions")
    except ProviderError as exc:
        raise ProviderError(
            str(exc), retryable=True, user_message=_MODEL_DOWN
        ) from exc

    try:
        choice = data["choices"][0]
        message = choice["message"]
        if not isinstance(message, dict):
            raise TypeError("message не объект")
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            f"неожиданная форма ответа ilons: {str(data)[:300]}",
            retryable=True,
            user_message=_MODEL_DOWN,
        ) from exc

    # Ответ берём только из content. reasoning_content — это размышления
    # модели, в них есть и черновые варианты полей, и JSON-подобный мусор;
    # разбирать их как результат нельзя.
    raw_content = message.get("content")
    content = raw_content.strip() if isinstance(raw_content, str) else ""
    finish = choice.get("finish_reason")

    if not content:
        reasoning = (message.get("reasoning_content") or "").strip()
        raise ProviderError(
            "ilons вернул пустой content"
            + (f" (finish_reason={finish}" if finish else " (")
            + f", рассуждений {len(reasoning)} символов): "
            "модель не дописала ответ, нужен retry или больший LLM_MAX_TOKENS",
            retryable=True,
            user_message=_MODEL_DOWN,
        )

    if finish == "length":
        # Ответ обрезан на полуслове: даже если JSON разобрался, часть полей
        # потеряна, а неполный документ хуже честной ошибки.
        raise ProviderError(
            f"ilons обрезал ответ по лимиту токенов (max_tokens="
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
        usage=data.get("usage", {}) or {},
        model=data.get("model", settings.llm_model),
    )
