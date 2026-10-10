"""LLM-провайдеры: cloud (ilons Qwen) и local (Ollama). Оба рабочие (TZ 7).

Здесь же общие помощники разбора ответа модели: модель возвращает текст,
а нам нужен строгий словарь полей. Ничего не достраиваем и не угадываем —
что не распознали, остаётся отсутствующим.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx

__all__ = [
    "parse_json_object",
    "ProviderError",
    "LLMResult",
    "describe_http_error",
    "response_json",
]


class ProviderError(RuntimeError):
    """Провайдер не ответил или ответил неразбираемым. Решение о retry — у воркера."""


def describe_http_error(exc: httpx.HTTPError) -> str:
    """Человекочитаемая причина сетевой ошибки.

    У таймаутов `str(exc)` пустой, поэтому одного текста исключения мало:
    в журнале останется «ilons: » без причины. Добавляем класс ошибки и,
    для ответов с кодом 4xx/5xx, тело — в нём обычно и есть объяснение сервера.
    """
    kind = type(exc).__name__
    if isinstance(exc, httpx.TimeoutException):
        return f"таймаут ({kind})"
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip().replace("\n", " ")[:300]
        return f"HTTP {exc.response.status_code}" + (f": {body}" if body else "")
    return f"{kind}: {exc}".rstrip(": ")


def response_json(resp: httpx.Response, who: str) -> dict:
    """Тело ответа как словарь, иначе ProviderError.

    `resp.json()` бросает ValueError, а не HTTPError: шлюз, отдавший 200 с
    HTML-страницей или обрезанным телом, иначе уронил бы воркер сырым
    исключением.
    """
    try:
        data = resp.json()
    except ValueError as exc:
        snippet = (resp.text or "").strip().replace("\n", " ")[:200]
        raise ProviderError(f"{who}: ответ не JSON: {snippet!r}") from exc
    if not isinstance(data, dict):
        raise ProviderError(f"{who}: ожидался JSON-объект, пришло {type(data).__name__}")
    return data


@dataclass(slots=True)
class LLMResult:
    """Ответ LLM-провайдера: извлечённые поля отдельно от служебных данных.

    Нужен, чтобы расход токенов и имя модели не подмешивались в словарь полей.
    """

    fields: dict
    usage: dict = field(default_factory=dict)
    model: str = ""


#: ```json ... ``` — частый способ модели «обернуть» ответ.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_json_object(raw: str) -> dict:
    """Вытащить JSON-объект из ответа модели.

    Если объекта нет — ProviderError: пустой словарь молча возвращать нельзя,
    иначе документ выглядел бы «разобранным без полей».
    """
    text = (raw or "").strip()
    if not text:
        raise ProviderError("модель вернула пустой ответ")

    for candidate in _json_candidates(text):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
        # Иногда модель оборачивает объект в список из одного элемента.
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            return data[0]

    raise ProviderError(f"в ответе модели нет JSON-объекта: {text[:200]!r}")


def _json_candidates(text: str) -> list[str]:
    """Кандидаты на разбор, от самого вероятного к самому грубому.

    Порядок: ответ целиком -> содержимое ```-обёртки -> первый по тексту
    сбалансированный объект -> от первой `{` до последней `}`.

    Первый сбалансированный объект стоит раньше «внешних скобок» не случайно:
    reasoning-модель может дописать после ответа ещё текст с фигурными
    скобками, и тогда жадный срез склеит ответ с мусором в неразбираемое.
    """
    candidates = [text]

    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))

    first = _first_balanced_object(text)
    if first:
        candidates.append(first)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    return candidates


def _first_balanced_object(text: str) -> str | None:
    """Первый сбалансированный {...} в тексте, с учётом строк и экранирования.

    Считать скобки «в лоб» нельзя: `{"issued_by": "ОВД {Басманный}"}` сломал бы
    наивный счётчик.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None
