"""Тест модельного слоя: реальный HTTP-код против подставных olmOCR и LLM."""

import asyncio
import functools
import io
import json
import os
import sys

# Мок-тесты не должны зависеть от локального .env разработчика (там может быть
# настоящий адрес сервера, ключ или MODEL_PROVIDER=local): переменные процесса
# приоритетнее .env, поэтому задаём их жёстко, а не через setdefault.
os.environ["OLMOCR_SERVER"] = "http://olmocr.test/v1"
os.environ["ILONS_API_KEY"] = "test-key"
os.environ["LLM_BASE_URL"] = "https://llm.test"
os.environ["MODEL_PROVIDER"] = "cloud"
os.environ["LLM_MAX_TOKENS"] = "2000"

import httpx
from PIL import Image

from app.config import settings
from app.enums import DocType
from app.model import recognize
from app.model.ocr import OcrError, to_png_pages
from app.model.providers import ProviderError

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


def jpeg(size=(800, 500)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="JPEG")
    return buf.getvalue()


def pdf() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), "white").save(buf, format="PDF")
    return buf.getvalue()


PASSPORT_TEXT = """---
primary_language: ru
is_rotation_valid: true
---
РОССИЙСКАЯ ФЕДЕРАЦИЯ
Серия 45 04  № 123456
ИВАНОВ ИВАН ИВАНОВИЧ
Дата рождения 01.01.1990
Место рождения ГОР. МОСКВА
"""

seen = {}


def make_transport(
    llm_body,
    ocr_status=200,
    llm_status=200,
    finish_reason="stop",
    reasoning=None,
):
    """Подставной сервер: отдельно olmOCR, отдельно LLM.

    `reasoning` имитирует reasoning-модель ilons: размышления приходят в
    отдельном поле, ответ — в content.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "olmocr.test":
            seen["ocr_request"] = json.loads(request.content)
            if ocr_status != 200:
                return httpx.Response(ocr_status, text="boom")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": PASSPORT_TEXT}}]},
            )
        seen["llm_request"] = json.loads(request.content)
        seen["llm_auth"] = request.headers.get("authorization")
        if llm_status != 200:
            return httpx.Response(llm_status, text="boom")

        message = {"content": llm_body}
        if reasoning is not None:
            message["reasoning_content"] = reasoning
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3.8-27B",
                "usage": {"total_tokens": 321},
                "choices": [{"message": message, "finish_reason": finish_reason}],
            },
        )

    return httpx.MockTransport(handler)


def patch_httpx(transport):
    """Подменить AsyncClient так, чтобы запросы шли в подставной транспорт."""
    original = httpx.AsyncClient
    httpx.AsyncClient = functools.partial(original, transport=transport)
    return original


async def main():
    print("\n1. Рендер входных файлов")
    check("JPEG -> одна PNG-страница", len(to_png_pages(jpeg())) == 1)
    check("PDF -> страницы отрендерены", len(to_png_pages(pdf())) >= 1)
    try:
        to_png_pages(b"\x00\x01\x02 not an image")
        check("битый ввод -> OcrError", False, "исключения не было")
    except OcrError as exc:
        check("битый ввод -> OcrError, а не падение", True, str(exc)[:60])

    print("\n2. Сквозной разбор: OCR -> LLM -> нормализованные поля")
    body = json.dumps({
        "series": "45 04", "number": "123456",
        "full_name": "ИВАНОВ ИВАН ИВАНОВИЧ",
        "birth_date": "1990-01-01", "birth_place": "ГОР. МОСКВА",
        "issued_by": None, "issue_date": None,
    }, ensure_ascii=False)
    original = patch_httpx(make_transport(body))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original

    check("серия нормализована", res.fields["series"] == "4504", res.fields["series"])
    check("дата приведена к ДД.ММ.ГГГГ", res.fields["birth_date"] == "01.01.1990",
          res.fields["birth_date"])
    check("ненайденное поле осталось None", res.fields["issue_date"] is None)
    check("провайдер отмечен", res.provider == "cloud", res.provider)
    check("usage сохранён", res.usage.get("total_tokens") == 321, str(res.usage))
    check("front matter olmOCR отрезан", not res.raw_text.startswith("---"),
          res.raw_text[:40])
    check("в raw_text есть текст документа", "ИВАНОВ" in res.raw_text)

    print("\n3. Форма запросов к серверам")
    ocr_req = seen["ocr_request"]
    check("в olmOCR уходит модель из env",
          ocr_req["model"] == settings.olmocr_model, ocr_req["model"])
    content = ocr_req["messages"][0]["content"]
    check("картинка уходит как data:image/png;base64",
          any(p.get("type") == "image_url"
              and p["image_url"]["url"].startswith("data:image/png;base64,")
              for p in content))
    check("ключ ilons уходит в Authorization",
          seen["llm_auth"] == "Bearer test-key", str(seen["llm_auth"]))
    check("в LLM уходит промпт из prompts/",
          "серия паспорта" in seen["llm_request"]["messages"][1]["content"])
    check("в LLM уходит OCR-текст",
          "ИВАНОВ ИВАН ИВАНОВИЧ" in seen["llm_request"]["messages"][1]["content"])

    print("\n4. Грязный ответ модели")
    dirty = (
        "Вот результат:\n```json\n"
        + json.dumps({
            "series": {"value": "4504", "confidence": 0.95},
            "number": "123456",
            "full_name": "ИВАНОВ ИВАН ИВАНОВИЧ",
            "birth_date": "01.01.1990",
            "отчество": "ИВАНОВИЧ",
            "comment": "это паспорт",
        }, ensure_ascii=False)
        + "\n```\nГотово."
    )
    original = patch_httpx(make_transport(dirty))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original
    check("JSON вынут из ```-обёртки и болтовни", res.fields["number"] == "123456")
    check("вложенный {value, confidence} разобран", res.fields["series"] == "4504")
    check("уверенность подхвачена", res.confidence.get("series") == 0.95,
          str(res.confidence))
    check("лишние ключи модели отброшены",
          set(res.fields) == {"series", "number", "full_name", "birth_date",
                              "birth_place", "issued_by", "issue_date"},
          str(sorted(res.fields)))

    print("\n5. Ошибки не роняют разбор")
    original = patch_httpx(make_transport(body, ocr_status=500))
    try:
        await recognize(DocType.PASSPORT_RF, jpeg())
        check("olmOCR 500 -> ProviderError", False, "исключения не было")
    except ProviderError as exc:
        check("olmOCR 500 -> ProviderError", True, str(exc)[:60])
    finally:
        httpx.AsyncClient = original

    original = patch_httpx(make_transport("я не умею в JSON, извините"))
    try:
        await recognize(DocType.PASSPORT_RF, jpeg())
        check("болтовня вместо JSON -> ProviderError", False, "исключения не было")
    except ProviderError as exc:
        check("болтовня вместо JSON -> ProviderError, без выдуманных полей",
              True, str(exc)[:60])
    finally:
        httpx.AsyncClient = original

    original = patch_httpx(make_transport(body, llm_status=502))
    try:
        await recognize(DocType.PASSPORT_RF, jpeg())
        check("LLM 502 -> ProviderError", False, "исключения не было")
    except ProviderError as exc:
        check("LLM 502 -> ProviderError", True, str(exc)[:60])
    finally:
        httpx.AsyncClient = original

    print("\n5a. Qwen у ilons — reasoning-модель")
    original = patch_httpx(make_transport(body))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original
    check("запас токенов не меньше 1500",
          seen["llm_request"]["max_tokens"] >= 1500,
          str(seen["llm_request"]["max_tokens"]))

    # Размышления содержат черновые, неверные значения полей. Если разобрать
    # reasoning_content вместо content, в БД попадёт мусор.
    reasoning = json.dumps({"series": "0000", "number": "999999"}, ensure_ascii=False)
    original = patch_httpx(make_transport(body, reasoning="Подумаем... " + reasoning))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original
    check("берём content, а не reasoning_content",
          res.fields["series"] == "4504" and res.fields["number"] == "123456",
          str(res.fields["series"]) + "/" + str(res.fields["number"]))

    original = patch_httpx(make_transport("", reasoning="думал, но не успел"))
    try:
        await recognize(DocType.PASSPORT_RF, jpeg())
        check("пустой content -> ошибка", False, "исключения не было")
    except ProviderError as exc:
        check("пустой content -> ошибка с подсказкой про LLM_MAX_TOKENS",
              "LLM_MAX_TOKENS" in str(exc), str(exc)[:90])
    finally:
        httpx.AsyncClient = original

    truncated = '{"series": "4504", "number": "123456", "full_na'
    original = patch_httpx(make_transport(truncated, finish_reason="length"))
    try:
        await recognize(DocType.PASSPORT_RF, jpeg())
        check("обрезанный ответ -> ошибка", False, "исключения не было")
    except ProviderError as exc:
        check("finish_reason=length -> ошибка, неполный документ не сохраняем",
              "обрезал" in str(exc), str(exc)[:90])
    finally:
        httpx.AsyncClient = original

    # Reasoning-модель любит дописать комментарий после ответа — иногда
    # с фигурными скобками.
    trailing = (
        json.dumps({"series": "4504", "number": "123456",
                    "full_name": "ИВАНОВ ИВАН", "birth_date": "01.01.1990"},
                   ensure_ascii=False)
        + '\n\nПримечание: место рождения не читается {неразборчиво}.'
    )
    original = patch_httpx(make_transport(trailing))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original
    check("берём первый сбалансированный объект, мусор после ответа не мешает",
          res.fields["number"] == "123456", str(res.fields))

    # Фигурные скобки внутри строкового значения не должны ломать разбор.
    braces = json.dumps({"series": "4504", "number": "123456",
                         "full_name": "ИВАНОВ ИВАН",
                         "birth_date": "01.01.1990",
                         "issued_by": "ОВД {Басманный}"}, ensure_ascii=False)
    original = patch_httpx(make_transport(braces))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original
    check("скобки внутри значения не ломают разбор",
          res.fields["issued_by"] == "ОВД {Басманный}", str(res.fields.get("issued_by")))

    print("\n6. Переключение провайдера только через env")
    def ollama_handler(request):
        if request.url.host == "olmocr.test":
            return httpx.Response(200, json={"choices": [{"message": {"content": PASSPORT_TEXT}}]})
        seen["ollama_path"] = request.url.path
        seen["ollama_body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "qwen2.5vl:3b",
            "eval_count": 42,
            "message": {"content": json.dumps({
                "series": "4504", "number": "123456",
                "full_name": "ИВАНОВ ИВАН", "birth_date": "01.01.1990"},
                ensure_ascii=False)},
        })

    settings.model_provider = "local"
    original = patch_httpx(httpx.MockTransport(ollama_handler))
    try:
        res = await recognize(DocType.PASSPORT_RF, jpeg())
    finally:
        httpx.AsyncClient = original
        settings.model_provider = "cloud"

    check("MODEL_PROVIDER=local уводит в Ollama",
          res.provider == "local" and seen["ollama_path"] == "/api/chat",
          f"{res.provider} {seen.get('ollama_path')}")
    check("Ollama просят вернуть JSON", seen["ollama_body"].get("format") == "json")
    check("поля разобраны локальной моделью", res.fields["number"] == "123456")
    check("OCR остался self-hosted в local-режиме",
          res.usage.get("ocr_model") == settings.olmocr_model)

    settings.model_provider = "нечто"
    seen.clear()
    seen_after = seen
    original = patch_httpx(make_transport(body))
    try:
        await recognize(DocType.PASSPORT_RF, jpeg())
        check("неизвестный провайдер -> ошибка", False, "исключения не было")
    except ProviderError as exc:
        check("неизвестный провайдер -> ошибка про MODEL_PROVIDER до вызова OCR",
              "MODEL_PROVIDER" in str(exc) and "ocr_request" not in seen_after,
              str(exc)[:70])
    finally:
        httpx.AsyncClient = original
        settings.model_provider = "cloud"

    print("\n7. Промпты есть на все 8 типов")
    from app.model import load_prompt
    missing = []
    for dt in DocType:
        try:
            load_prompt(dt)
        except ProviderError:
            missing.append(dt.value)
    check("8 промптов читаются", not missing, str(missing))

    print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
