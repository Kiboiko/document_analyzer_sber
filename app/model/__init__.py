"""Модельный слой — единственная точка вызова модели (TZ 7).

Контракт: `recognize(doc_type, image_bytes) -> ExtractResult`.
Шаги: step1 OCR (olmOCR -> текст), step2 LLM извлекает JSON по промпту
`prompts/<doc_type>.txt`. Провайдер LLM выбирается `MODEL_PROVIDER` из env,
не константой в коде. Валидация полей — в `validate.py`, не в промпте.

Владелец: Степан (ветка feat/model-intake).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

from app.enums import DocType
from app.schemas import ExtractResult

__all__ = ["recognize", "load_prompt", "get_provider"]


def load_prompt(doc_type: DocType) -> str:
    """Прочитать `prompts/<doc_type>.txt`. Промпты живут в репо."""
    raise NotImplementedError  # TODO(Степан)


def get_provider():
    """Вернуть LLM-провайдера по MODEL_PROVIDER: cloud | local.

    Смена провайдера — только через env, без правки кода.
    """
    raise NotImplementedError  # TODO(Степан)


async def recognize(doc_type: DocType, image_bytes: bytes) -> ExtractResult:
    """Распознать документ: OCR -> LLM -> поля.

    Ошибка провайдера или битый ввод поднимаются наружу как исключение:
    решение про retry и статус принимает воркер. Выдуманных данных не возвращаем.
    """
    raise NotImplementedError  # TODO(Степан)
