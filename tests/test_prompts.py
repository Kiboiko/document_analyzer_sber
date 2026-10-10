"""Промпты согласованы с контрактом полей (enums.EXTRACTED_FIELDS) и друг с другом."""

import re
import sys

from app.enums import EXTRACTED_FIELDS, DocType
from app.model import load_prompt
from app.model.providers import parse_json_object
from app.model.validate import DATE_FIELDS

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


for doc_type in DocType:
    fields = EXTRACTED_FIELDS[doc_type]
    text = load_prompt(doc_type)
    title = doc_type.value
    print(f"\n{title}")

    head = text.split("Правила:")[0]
    described = re.findall(r"^\s{2}([a-z_]+)\s+—", head, re.M)
    check(f"{title}: описаны ровно поля из enums, в том же порядке",
          tuple(described) == fields, f"{described} != {list(fields)}")

    template = text.split("Формат ответа")[-1]
    try:
        keys = tuple(parse_json_object(template[template.index("{"):]).keys())
        check(f"{title}: ключи шаблона ответа совпадают с enums", keys == fields, str(keys))
    except Exception as exc:  # noqa: BLE001
        check(f"{title}: шаблон ответа разбирается", False, str(exc)[:80])

    has_dates = any(f in DATE_FIELDS for f in fields)
    check(f"{title}: формат дат указан ровно когда есть поля-даты",
          ("ДД.ММ.ГГГГ" in text) == has_dates)

    check(f"{title}: не просит выдумывать (есть правило про null)", "null" in text)
    check(f"{title}: просит только JSON", "только объект" in text)

    # Правила проверки (срок, формат серии) живут в validate.py, не в промпте.
    leaked = [w for w in ("просроч", "истёк", "не в будущем", "валидн")
              if w in text.lower()]
    check(f"{title}: нет правил валидации", not leaked, str(leaked))

print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
