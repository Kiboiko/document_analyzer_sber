"""Живой прогон recognize() против настоящих OCR-сервиса и LLM (не мок).

Нужны в .env: ILONS_API_KEY (или MODEL_PROVIDER=local). OCR_SERVICE_URL по
умолчанию https://ocr.ilons.ru; OCR_CLIENT_TOKEN нужен, только если сервис
закрыт. Шаги независимы: без ключа LLM проверка OCR всё равно выполнится.

    python tests/live_check.py <doc_type> <путь к файлу> [...ещё пары]

Текст от OCR и итоговый результат пишутся в data/live/ (каталог в .gitignore):
в них персональные данные, в репозиторий они попадать не должны.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx

from app.config import settings
from app.enums import DocType
from app.model import get_provider, ocr, recognize
from app.model.providers import ProviderError
from app.model.validate import validate_document

OUT = Path("data/live")


async def check_ocr_health() -> None:
    """GET /health сервиса: жив ли он и какая модель за ним."""
    url = f"{settings.ocr_service_url.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
        print(f"OCR {url}: HTTP {resp.status_code} {resp.text.strip()[:200]}")
    except httpx.HTTPError as exc:
        print(f"OCR {url}: недоступен ({type(exc).__name__}: {exc})")


async def check_model_id() -> None:
    """LLM_MODEL должен точно совпадать с id из GET /v1/models."""
    if settings.model_provider != "cloud":
        return
    try:
        models = await get_provider().list_models()
    except ProviderError as exc:
        print(f"LLM: список моделей недоступен — {exc}")
        return
    print(f"id моделей на сервере: {models}")
    if settings.llm_model in models:
        print(f"OK: LLM_MODEL={settings.llm_model!r} есть в списке")
    else:
        print(f"РАСХОЖДЕНИЕ: LLM_MODEL={settings.llm_model!r} в списке нет — "
              "поправить config.py и .env.example")


def print_ocr_error(exc: ProviderError) -> None:
    print(f"OCR: {exc}")
    print(f"  повторять имеет смысл: {exc.retryable}")
    print(f"  текст для соискателя: {exc.user_message!r}")


async def run_one(doc_type: DocType, path: Path) -> None:
    print(f"\n=== {doc_type.value}: {path.name}")
    data = path.read_bytes()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")

    # Шаг 1: OCR отдельно, чтобы текст сохранился, даже если LLM не ответит.
    started = datetime.now()
    try:
        text = await ocr.ocr_document(data)
    except ProviderError as exc:
        print_ocr_error(exc)
        return
    seconds = (datetime.now() - started).total_seconds()
    (OUT / f"{stamp}_{doc_type.value}_ocr.md").write_text(text, encoding="utf-8")
    print(f"OCR: {len(text)} символов за {seconds:.1f} с (сохранено в data/live/)")

    # Шаг 2: весь путь. OCR второй раз не гоняем — отдаём уже полученный текст.
    original = ocr.ocr_document

    async def cached(_: bytes) -> str:
        return text

    ocr.ocr_document = cached
    try:
        result = await recognize(doc_type, data)
    except ProviderError as exc:
        print(f"LLM/разбор: {exc}")
        return
    finally:
        ocr.ocr_document = original

    report = validate_document(doc_type, result.fields)
    print(f"провайдер: {result.provider}, usage: {result.usage}")
    for name, value in result.fields.items():
        print(f"  {name:12} = {value!r}")
    print(f"статус: {report.status.value}")
    if report.missing_required:
        print(f"  нет минимума: {report.missing_required}")
    for note in report.notes:
        print(f"  замечание: {note}")

    (OUT / f"{stamp}_{doc_type.value}_result.json").write_text(
        json.dumps({"fields": result.fields, "status": report.status.value,
                    "notes": report.notes, "usage": result.usage},
                   ensure_ascii=False, indent=2), encoding="utf-8")


async def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) % 2:
        print(__doc__)
        return 2
    await check_ocr_health()
    await check_model_id()
    for i in range(0, len(argv), 2):
        await run_one(DocType(argv[i]), Path(argv[i + 1]))
    return 0


sys.exit(asyncio.run(main(sys.argv[1:])))
