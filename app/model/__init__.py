"""Модельный слой — единственная точка вызова модели (TZ 7).

Контракт: `recognize(doc_type, image_bytes) -> ExtractResult`.
Шаги: step1 OCR (HTTP-сервис -> текст), step2 LLM извлекает JSON по промпту
`prompts/<doc_type>.txt`. Провайдер LLM выбирается `MODEL_PROVIDER` из env,
не константой в коде. Валидация полей — в `validate.py`, не в промпте.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from types import ModuleType

from app.config import settings
from app.enums import EXTRACTED_FIELDS, DocType
from app.model import ocr
from app.model.providers import ProviderError
from app.model.validate import normalize_fields
from app.schemas import ExtractResult

log = logging.getLogger(__name__)

__all__ = ["recognize", "load_prompt", "get_provider", "ProviderError"]

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=len(DocType))
def load_prompt(doc_type: DocType) -> str:
    """Прочитать `prompts/<doc_type>.txt`. Промпты живут в репо, не в коде."""
    path = PROMPTS_DIR / f"{doc_type.value}.txt"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProviderError(f"не прочитать промпт {path.name}: {exc}") from exc
    if not text:
        raise ProviderError(f"промпт {path.name} пуст")
    return text


def get_provider() -> ModuleType:
    """Вернуть LLM-провайдера по MODEL_PROVIDER: cloud | local.

    Смена провайдера — только через env, без правки кода.
    """
    name = (settings.model_provider or "").strip().lower()
    if name == "cloud":
        from app.model.providers import cloud

        return cloud
    if name == "local":
        from app.model.providers import local

        return local
    raise ProviderError(
        f"MODEL_PROVIDER={settings.model_provider!r}: ожидается 'cloud' или 'local'"
    )


def _pick_fields(doc_type: DocType, raw: dict) -> tuple[dict, dict]:
    """Оставить только поля своего типа документа; вытащить уверенность.

    Модель охотно добавляет лишние ключи и переименовывает нужные — всё, что
    не входит в контракт типа, отбрасываем. Выдуманных полей в БД быть не должно.
    """
    allowed = EXTRACTED_FIELDS[doc_type]
    lowered = {str(key).strip().lower(): value for key, value in raw.items()}

    fields: dict[str, object] = {}
    confidence: dict[str, float] = {}

    for name in allowed:
        value = lowered.get(name)

        # Вид {"number": {"value": "...", "confidence": 0.9}} тоже встречается.
        if isinstance(value, dict):
            raw_conf = value.get("confidence")
            value = value.get("value")
            if isinstance(raw_conf, (int, float)) and 0.0 <= float(raw_conf) <= 1.0:
                confidence[name] = float(raw_conf)

        fields[name] = value

    # Либо отдельным словарём: {"confidence": {"number": 0.9}}.
    raw_conf_map = lowered.get("confidence")
    if isinstance(raw_conf_map, dict):
        for name, value in raw_conf_map.items():
            key = str(name).strip().lower()
            if key in allowed and isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
                confidence.setdefault(key, float(value))

    dropped = set(lowered) - set(allowed) - {"confidence"}
    if dropped:
        log.debug("%s: отброшены лишние ключи модели: %s", doc_type.value, sorted(dropped))

    return fields, confidence


async def recognize(doc_type: DocType, image_bytes: bytes) -> ExtractResult:
    """Распознать документ: OCR -> LLM -> нормализованные поля.

    Ошибка провайдера или битый ввод поднимаются наружу как `ProviderError`:
    решение про retry и статус документа принимает воркер. Выдуманных данных
    не возвращаем — нераспознанное поле остаётся `None`.
    """
    # Провайдера и промпт проверяем до OCR: ошибку конфигурации надо увидеть
    # сразу, а не после самого долгого шага.
    provider = get_provider()
    prompt = load_prompt(doc_type)

    # Файл уходит в OCR как есть: рендер страниц и приведение к PDF делает
    # сам сервис. Пустой текст он здесь же превращает в OcrError.
    raw_text = await ocr.ocr_document(image_bytes)

    result = await provider.complete_json(prompt, raw_text)

    fields, confidence = _pick_fields(doc_type, result.fields)

    return ExtractResult(
        fields=normalize_fields(doc_type, fields),
        raw_text=raw_text,
        provider=provider.NAME,
        usage={
            **result.usage,
            "model": result.model,
            "ocr_service": settings.ocr_service_url,
        },
        confidence=confidence,
    )
