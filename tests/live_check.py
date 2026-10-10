"""Живой прогон recognize() против настоящих olmOCR и LLM (не мок).

Нужны в .env: OLMOCR_SERVER, ILONS_API_KEY (или MODEL_PROVIDER=local).

    python tests/live_check.py <doc_type> <путь к файлу> [...ещё пары]

Сырые ответы olmOCR и итоговый результат пишутся в data/live/ (каталог в
.gitignore): в них персональные данные, в репозиторий они попадать не должны.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.enums import DocType
from app.model import get_provider, ocr, recognize
from app.model.providers import ProviderError
from app.model.validate import validate_document

OUT = Path("data/live")


async def check_model_id() -> None:
    """LLM_MODEL должен точно совпадать с id из GET /v1/models."""
    if settings.model_provider != "cloud":
        return
    models = await get_provider().list_models()
    print(f"id моделей на сервере: {models}")
    if settings.llm_model in models:
        print(f"OK: LLM_MODEL={settings.llm_model!r} есть в списке")
    else:
        print(f"РАСХОЖДЕНИЕ: LLM_MODEL={settings.llm_model!r} в списке нет — "
              "поправить config.py и .env.example")


async def run_one(doc_type: DocType, path: Path) -> None:
    print(f"\n=== {doc_type.value}: {path.name}")
    data = path.read_bytes()

    # Сырой ответ olmOCR по страницам — до любой нашей обработки.
    raw_pages = []
    for page in ocr.to_png_pages(data):
        raw_pages.append(await ocr.ocr_text(page))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    (OUT / f"{stamp}_{doc_type.value}_ocr.txt").write_text(
        "\n\n----- page -----\n\n".join(raw_pages), encoding="utf-8")
    print(f"текст OCR: {sum(len(p) for p in raw_pages)} символов "
          f"(сохранён в data/live/)")

    try:
        result = await recognize(doc_type, data)
    except ProviderError as exc:
        print(f"ProviderError: {exc}")
        return

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
    try:
        await check_model_id()
        for i in range(0, len(argv), 2):
            await run_one(DocType(argv[i]), Path(argv[i + 1]))
    except ProviderError as exc:
        print(f"Остановлено: {exc}")
        return 1
    return 0


sys.exit(asyncio.run(main(sys.argv[1:])))
