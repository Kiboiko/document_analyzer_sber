"""Ошибки провайдеров: cloud, local и OCR одинаково не падают и не "готовят" документ.

Каждый сценарий гоняется через настоящий `recognize()` с подставным HTTP:
код запросов и разбора ответов реальный, сеть замокана.

Инварианты:
1. любая сбойная ситуация -> ProviderError с понятным текстом, и ничего другого
   (ни ValueError, ни AttributeError, ни KeyError);
2. пустой, обрезанный или бессмысленный ответ модели не превращается в
   документ со статусом `done`.
"""

import asyncio
import functools
import io
import json
import os
import sys

# Не зависим от локального .env: переменные процесса приоритетнее.
os.environ["OCR_SERVICE_URL"] = "http://ocr.test"
os.environ["OCR_CLIENT_TOKEN"] = "ocr-test-token"
os.environ["ILONS_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://llm.test"
os.environ["OLLAMA_HOST"] = "http://ollama.test:11434"
os.environ["MODEL_PROVIDER"] = "cloud"
os.environ["LLM_MAX_TOKENS"] = "2000"

import httpx
from PIL import Image

from app.config import settings
from app.enums import DocStatus, DocType
from app.model import recognize
from app.model.ocr import OcrError
from app.model.providers import ProviderError
from app.model.validate import validate_document

OK, FAIL = [], []

GOOD_FIELDS = {"series": "4504", "number": "123456", "full_name": "ИВАНОВ ИВАН",
               "birth_date": "01.01.1990"}
OCR_TEXT = "Серия 45 04 № 123456 ИВАНОВ ИВАН ИВАНОВИЧ 01.01.1990"


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buf, format="JPEG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Ответы LLM по сценариям. Для каждого провайдера — свой формат тела.
# --------------------------------------------------------------------------- #


def llm_ok(provider, content, finish="stop"):
    if provider == "cloud":
        return httpx.Response(200, json={
            "model": "m", "usage": {},
            "choices": [{"message": {"content": content}, "finish_reason": finish}]})
    return httpx.Response(200, json={
        "model": "m", "done_reason": finish,
        "message": {"content": content}})


def scenario(provider, kind, request):
    """Ответ (или исключение) подставного LLM-сервера."""
    full = json.dumps(GOOD_FIELDS, ensure_ascii=False)
    match kind:
        case "timeout":
            raise httpx.ReadTimeout("", request=request)
        case "connect":
            raise httpx.ConnectError("connection refused", request=request)
        case "http500":
            return httpx.Response(500, text="upstream exploded")
        case "http503":
            return httpx.Response(503, text="overloaded")
        case "http401":
            return httpx.Response(401, text='{"error":"bad key"}')
        case "html200":
            return httpx.Response(200, text="<html><body>Bad gateway</body></html>",
                                  headers={"content-type": "text/html"})
        case "truncated_body":
            return httpx.Response(200, text='{"choices": [{"message"',
                                  headers={"content-type": "application/json"})
        case "json_list":
            return httpx.Response(200, json=[])
        case "empty_content":
            return llm_ok(provider, "")
        case "null_content":
            return llm_ok(provider, None)
        case "blank_content":
            return llm_ok(provider, "   \n  ")
        case "length_partial":
            return llm_ok(provider, '{"series": "4504", "number": "12', "length")
        case "length_valid_json":
            # Самый коварный случай: ответ обрезан, но JSON валиден и не пуст.
            return llm_ok(provider, json.dumps({"series": "4504"}), "length")
        case "prose":
            return llm_ok(provider, "Извините, я не смог разобрать этот документ.")
        case "error_in_body":
            return httpx.Response(200, json={"error": "model crashed"})
    raise AssertionError(kind)


def ocr_response(kind, request):
    """Ответ (или исключение) подставного OCR-сервиса. Коды — как у ocr.ilons.ru."""
    match kind:
        case "timeout":
            raise httpx.ReadTimeout("", request=request)
        case "connect":
            raise httpx.ConnectError("connection refused", request=request)
        case "http400":
            return httpx.Response(400, json={"detail": "uploaded file is not a readable PDF"})
        case "http401":
            return httpx.Response(401, json={"detail": "invalid token"})
        case "http413":
            return httpx.Response(413, json={"detail": "file too large"})
        case "http502":
            return httpx.Response(502, json={"detail": "vLLM error"})
        case "http504":
            return httpx.Response(504, json={"detail": "OCR timed out"})
        case "http500":
            return httpx.Response(500, text="internal error")
        case "http404":
            return httpx.Response(404, json={"detail": "Not Found"})
        case "http502_html":
            return httpx.Response(502, text="<html>Bad Gateway</html>",
                                  headers={"content-type": "text/html"})
        case "empty_text":
            return httpx.Response(200, text="   ",
                                  headers={"content-type": "text/markdown"})
    return httpx.Response(200, text=OCR_TEXT,
                          headers={"content-type": "text/markdown; charset=utf-8"})


def make_handler(provider, kind=None, ocr_kind=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ocr.test":
            return ocr_response(ocr_kind, request)
        return scenario(provider, kind, request)
    return handler


class patched:
    def __init__(self, handler):
        self.transport = httpx.MockTransport(handler)

    def __enter__(self):
        self.original = httpx.AsyncClient
        httpx.AsyncClient = functools.partial(self.original, transport=self.transport)

    def __exit__(self, *exc):
        httpx.AsyncClient = self.original


async def expect_provider_error(title, handler, needle):
    """recognize() должен кончиться ProviderError с нужным текстом."""
    try:
        with patched(handler):
            await recognize(DocType.PASSPORT_RF, jpeg())
    except ProviderError as exc:
        check(title, needle.lower() in str(exc).lower(), str(exc)[:110])
        check(f"{title}: retryable задан", isinstance(exc.retryable, bool))
        check(f"{title}: user_message задан", bool(exc.user_message))
    except Exception as exc:  # noqa: BLE001
        check(title, False, f"сырое {type(exc).__name__}: {str(exc)[:80]}")
    else:
        check(title, False, "исключения не было — документ прошёл")


# --------------------------------------------------------------------------- #

EXPECTED = {
    "timeout": "таймаут",
    "connect": "ConnectError",
    "http500": "HTTP 500",
    "http503": "HTTP 503",
    "http401": "HTTP 401",
    "html200": "не JSON",
    "truncated_body": "не JSON",
    "json_list": "JSON-объект",
    "empty_content": "пустой",
    "null_content": "пустой",
    "blank_content": "пустой",
    "length_partial": "обрезал",
    "length_valid_json": "обрезал",
    "prose": "нет JSON",
    "error_in_body": "",   # у cloud — неожиданная форма, у local — текст ошибки
}


async def main():
    for provider in ("cloud", "local"):
        settings.model_provider = provider
        print(f"\n=== LLM-провайдер: {provider}")
        for kind, needle in EXPECTED.items():
            if kind == "error_in_body":
                needle = "неожиданная форма" if provider == "cloud" else "model crashed"
            await expect_provider_error(
                f"{provider}: {kind}", make_handler(provider, kind), needle)
    settings.model_provider = "cloud"

    print("\n=== OCR-сервис: каждый код -> понятная ошибка с признаками для воркера")
    # (сценарий, что должно быть в тексте для журнала, имеет ли смысл повтор)
    ocr_cases = [
        ("http400", "HTTP 400", False),       # нечитаемый файл
        ("http401", "OCR_CLIENT_TOKEN", False),
        ("http413", "HTTP 413", False),
        ("http502", "HTTP 502", True),         # ошибка vLLM у них
        ("http504", "HTTP 504", True),         # их дедлайн
        ("http500", "HTTP 500", True),
        ("http502_html", "HTTP 502", True),    # шлюз вместо JSON
        ("http404", "HTTP 404", False),        # неверный OCR_SERVICE_URL
        ("timeout", "таймаут", True),
        ("connect", "ConnectError", True),
        ("empty_text", "пустой текст", False),
    ]
    # Технические детали не должны попадать к соискателю.
    internals = ("HTTP", "vLLM", "PDF", "OCR_", "ocr.", "token", "Token")
    for kind, needle, retryable in ocr_cases:
        llm_calls = []
        inner = make_handler("cloud", "prose", ocr_kind=kind)

        def watcher(request, inner=inner, llm_calls=llm_calls):
            if request.url.host != "ocr.test":
                llm_calls.append(request.url.host)
            return inner(request)

        try:
            with patched(watcher):
                await recognize(DocType.PASSPORT_RF, jpeg())
            check(f"OCR {kind}: исключения не было", False)
            continue
        except OcrError as exc:
            title = f"OCR {kind}"
            check(f"{title}: OcrError с причиной", needle.lower() in str(exc).lower(),
                  str(exc)[:90])
            check(f"{title}: retryable={retryable}", exc.retryable is retryable,
                  str(exc.retryable))
            msg = exc.user_message or ""
            check(f"{title}: есть текст для соискателя", len(msg) > 10, msg)
            check(f"{title}: в нём нет технических деталей",
                  not any(w in msg for w in internals), msg)
            check(f"{title}: к LLM запрос не уходит", not llm_calls, str(llm_calls))
        except Exception as exc:  # noqa: BLE001
            check(f"OCR {kind}: OcrError", False, f"сырое {type(exc).__name__}: {str(exc)[:70]}")

    print("\n=== OCR: пустой файл не отправляется на сервис")
    sent = []

    def spy(request):
        sent.append(request.url.host)
        return ocr_response(None, request)

    try:
        with patched(spy):
            await recognize(DocType.PASSPORT_RF, b"")
        check("пустой файл -> ошибка", False)
    except OcrError as exc:
        check("пустой файл -> OcrError, retryable=False", exc.retryable is False, str(exc))
        check("пустой файл: запросов наружу нет", not sent, str(sent))

    print("\n=== OCR: токен не попадает в сообщения об ошибках")
    try:
        with patched(make_handler("cloud", "prose", ocr_kind="http401")):
            await recognize(DocType.PASSPORT_RF, jpeg())
    except OcrError as exc:
        check("токен не утекает в текст ошибки",
              "ocr-test-token" not in str(exc) and "ocr-test-token" not in (exc.user_message or ""))

    print("\n=== cloud: шлюз не знает response_format -> повтор без него")
    state = {"n": 0, "bodies": []}

    def retry_handler(request):
        if request.url.host == "ocr.test":
            return make_handler("cloud", "prose")(request)
        state["n"] += 1
        state["bodies"].append(json.loads(request.content))
        if state["n"] == 1:
            return httpx.Response(400, text="response_format is not supported")
        return llm_ok("cloud", json.dumps(GOOD_FIELDS, ensure_ascii=False))

    with patched(retry_handler):
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    check("второй запрос ушёл без response_format",
          state["n"] == 2 and "response_format" not in state["bodies"][1], str(state["n"]))
    check("результат получен", res.fields["number"] == "123456")

    print("\n=== cloud: нет ключа -> понятная ошибка, а не 401 от сервера")
    saved = settings.ilons_api_key
    settings.ilons_api_key = ""
    try:
        await expect_provider_error("cloud: ILONS_API_KEY пуст",
                                    make_handler("cloud", "prose"), "ILONS_API_KEY")
    finally:
        settings.ilons_api_key = saved

    print("\n=== local: лимит токенов передаётся в Ollama")
    seen = {}

    def local_spy(request):
        if request.url.host == "ocr.test":
            return make_handler("local", "prose")(request)
        seen["body"] = json.loads(request.content)
        return llm_ok("local", json.dumps(GOOD_FIELDS, ensure_ascii=False))

    settings.model_provider = "local"
    try:
        with patched(local_spy):
            await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        settings.model_provider = "cloud"
    check("в options есть num_predict",
          seen["body"]["options"].get("num_predict") == settings.llm_max_tokens,
          str(seen["body"]["options"]))

    print("\n=== Документ не становится «готово» без реальных данных")
    # Модель отвечает формально корректно, но по сути пусто или мимо.
    hollow = {
        "все поля null": {k: None for k in
                          ("series", "number", "full_name", "birth_date",
                           "birth_place", "issued_by", "issue_date")},
        "пустой объект": {},
        "чужие ключи": {"результат": "паспорт", "комментарий": "читается плохо"},
        "только необязательные": {"birth_place": "Москва", "issued_by": "ОВД"},
        "строки-заглушки": {"series": "не указано", "number": "-",
                            "full_name": "null", "birth_date": "нет"},
    }
    for provider in ("cloud", "local"):
        settings.model_provider = provider
        for title, payload in hollow.items():
            content = json.dumps(payload, ensure_ascii=False)

            def h(request, provider=provider, content=content):
                if request.url.host == "ocr.test":
                    return make_handler(provider, "prose")(request)
                return llm_ok(provider, content)

            with patched(h):
                result = await recognize(DocType.PASSPORT_RF, jpeg())
            report = validate_document(DocType.PASSPORT_RF, result.fields)
            check(f"{provider}: «{title}» -> error, не done",
                  report.status == DocStatus.ERROR and bool(report.missing_required),
                  f"статус={report.status.value}, нет={report.missing_required}")
    settings.model_provider = "cloud"

    print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
