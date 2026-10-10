"""Правила разбора ответа модели и проверки полей по типу документа (TZ 3, 7).

Два шага и оба здесь, а не в промпте:
1. нормализация — привести значение к одному виду (даты к ДД.ММ.ГГГГ и т.п.);
2. проверка — сказать, валидно ли значение, и почему нет.

Разделение статусов:
- нет поля из `MIN_FIELDS` -> документ не разобран, `error`;
- поле есть, но нарушает правило (в том числе просрочка) -> `flagged`;
  просрочка не ошибка приёма, документ принимается и разбирается.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from app.enums import FIELD_TITLES, MIN_FIELDS, DocStatus, DocType
from app.schemas import FieldValidation, ValidationReport

__all__ = [
    "normalize_field",
    "normalize_fields",
    "parse_date",
    "validate_field",
    "validate_document",
]

# --------------------------------------------------------------------------- #
# Нормализация
# --------------------------------------------------------------------------- #

#: Поля-даты: нормализуются к ДД.ММ.ГГГГ и проверяются как даты.
DATE_FIELDS = frozenset(
    {"birth_date", "issue_date", "expiry_date", "valid_until", "entry_date"}
)

#: Где номер — это только цифры (у паспорта УЗ или eu_id есть буквы, там нельзя).
_DIGIT_ONLY_NUMBER: dict[DocType, frozenset[str]] = {
    DocType.PASSPORT_RF: frozenset({"series", "number"}),
    DocType.SNILS: frozenset({"number"}),
    DocType.INN: frozenset({"number"}),
}

#: Значения, которыми модель обозначает «не нашёл». В поле должно попасть None.
_EMPTY_MARKERS = frozenset(
    {"", "-", "—", "–", "null", "none", "nan", "n/a", "нет", "не указано",
     "отсутствует", "не найдено", "неизвестно", "unknown"}
)

_DATE_FORMATS = (
    "%d.%m.%Y",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%Y.%m.%d",
    "%d.%m.%y",
)

_WS_RE = re.compile(r"\s+")
_LATIN_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\s'’\-\.]*$")

#: Бессрочный документ. «Срок не истёк» для него выполняется всегда, а в
#: минимальный набор ВНЖ срок действия входит — поэтому это не ошибка.
_INDEFINITE = frozenset(
    {"бессрочно", "бессрочный", "бессрочная", "без срока", "не ограничен",
     "unlimited", "permanent", "indefinite"}
)


def _clean(value: object) -> str | None:
    """Строка без лишних пробелов, либо None для «пустых» значений модели."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        return None

    # Неразрывные пробелы из OCR мешают и сравнению, и regexp.
    text = text.replace(" ", " ").replace(" ", " ")
    text = _WS_RE.sub(" ", text).strip().strip(",;")
    if text.lower() in _EMPTY_MARKERS:
        return None
    return text or None


def parse_date(value: str | None) -> date | None:
    """Разобрать дату в любом из принятых форматов. None — если не дата."""
    text = _clean(value)
    if not text:
        return None

    # «01.01.1990 г.» и «01.01.1990г» — обычный вид в русских документах.
    text = re.sub(r"\s*г\.?$", "", text, flags=re.IGNORECASE).strip()
    text = text.replace(" ", "")

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        # %y трактует 00-68 как 2000-е: для даты рождения это обычно неверно,
        # но угадывать век мы не вправе — отдаём как разобралось.
        return parsed
    return None


def normalize_field(doc_type: DocType, name: str, value: object) -> str | None:
    """Привести значение поля к каноническому виду.

    Нормализация не выдумывает данные: если значение не распознано как дата
    или номер, оно остаётся как есть и отвалится уже на проверке.
    """
    text = _clean(value)
    if text is None:
        return None

    if name in DATE_FIELDS:
        parsed = parse_date(text)
        return parsed.strftime("%d.%m.%Y") if parsed else text

    if name in _DIGIT_ONLY_NUMBER.get(doc_type, frozenset()):
        digits = re.sub(r"\D", "", text)
        if not digits:
            return text
        if doc_type is DocType.SNILS and len(digits) == 11:
            # Принятый формат СНИЛС: XXX-XXX-XXX YY.
            return f"{digits[0:3]}-{digits[3:6]}-{digits[6:9]} {digits[9:11]}"
        return digits

    # Регистр и ё/е у ФИО не трогаем: оператор должен видеть то же, что в
    # документе. Приведение к сравнимому виду — забота services/crosscheck.py.
    return text


def normalize_fields(
    doc_type: DocType, fields: dict[str, object]
) -> dict[str, str | None]:
    """Нормализовать все поля документа."""
    return {
        name: normalize_field(doc_type, name, value) for name, value in fields.items()
    }


# --------------------------------------------------------------------------- #
# Проверки
# --------------------------------------------------------------------------- #


def _title(name: str) -> str:
    return FIELD_TITLES.get(name, name)


def _ok(name: str) -> FieldValidation:
    return FieldValidation(name=name, is_valid=True)


def _bad(name: str, message: str) -> FieldValidation:
    return FieldValidation(name=name, is_valid=False, message=message)


def _check_digits(name: str, value: str, count: int) -> FieldValidation:
    digits = re.sub(r"\D", "", value)
    if len(digits) != count:
        return _bad(name, f"{_title(name)}: ожидается {count} цифр, получено {len(digits)}")
    return _ok(name)


def _check_not_future(name: str, value: str, today: date) -> FieldValidation:
    parsed = parse_date(value)
    if parsed is None:
        return _bad(name, f"{_title(name)}: не распознано как дата")
    if parsed > today:
        return _bad(name, f"{_title(name)}: дата в будущем")
    return _ok(name)


def _check_not_expired(name: str, value: str, today: date) -> FieldValidation:
    """Просрочка — замечание, а не отказ в приёме: документ уйдёт в `flagged`."""
    if value.strip().lower().rstrip(".") in _INDEFINITE:
        return _ok(name)

    parsed = parse_date(value)
    if parsed is None:
        return _bad(name, f"{_title(name)}: не распознано как дата")
    if parsed < today:
        return _bad(name, f"{_title(name)}: срок действия истёк ({value})")
    return _ok(name)


def _check_latin(name: str, value: str) -> FieldValidation:
    if not _LATIN_NAME_RE.match(value):
        return _bad(name, f"{_title(name)}: ожидается латиница")
    return _ok(name)


def validate_field(
    doc_type: DocType,
    name: str,
    value: str | None,
    today: date | None = None,
) -> FieldValidation:
    """Проверить одно поле правилами своего типа документа (TZ 3).

    Пустое поле здесь валидно: нехватку обязательных полей считает
    `validate_document` — только оно знает минимальный набор.
    """
    today = today or date.today()
    text = _clean(value)
    if text is None:
        return _ok(name)

    match (doc_type, name):
        case (DocType.PASSPORT_RF, "series"):
            return _check_digits(name, text, 4)
        case (DocType.PASSPORT_RF, "number"):
            return _check_digits(name, text, 6)
        case (DocType.PASSPORT_RF, "birth_date"):
            return _check_not_future(name, text, today)

        case (DocType.SNILS, "number"):
            return _check_digits(name, text, 11)

        case (DocType.INN, "number"):
            return _check_digits(name, text, 12)

        case (DocType.PASSPORT_UZ, "expiry_date"):
            return _check_not_expired(name, text, today)
        case (DocType.PASSPORT_UZ, "birth_date"):
            return _check_not_future(name, text, today)

        case (DocType.MIGRATION_CARD, "entry_date"):
            return _check_not_future(name, text, today)

        case (DocType.PATENT, "valid_until"):
            return _check_not_expired(name, text, today)

        case (DocType.EU_ID, "full_name"):
            return _check_latin(name, text)
        case (DocType.EU_ID, "expiry_date"):
            return _check_not_expired(name, text, today)
        case (DocType.EU_ID, "birth_date"):
            return _check_not_future(name, text, today)

        case (DocType.RESIDENCE_PERMIT, "expiry_date"):
            return _check_not_expired(name, text, today)

    # Прочие даты проверяем хотя бы на разбираемость — иначе в карточке
    # оператора появится мусор, который никто не пометит.
    if name in DATE_FIELDS and parse_date(text) is None:
        return _bad(name, f"{_title(name)}: не распознано как дата")

    return _ok(name)


def validate_document(
    doc_type: DocType,
    fields: dict[str, str | None],
    today: date | None = None,
) -> ValidationReport:
    """Собрать отчёт: статус документа, замечания по полям, нехватка минимума."""
    today = today or date.today()

    checks = [
        validate_field(doc_type, name, value, today=today)
        for name, value in fields.items()
    ]

    missing = [
        name
        for name in MIN_FIELDS[doc_type]
        if _clean(fields.get(name)) is None
    ]

    notes = [check.message for check in checks if check.message]

    if missing:
        # Без минимального набора документ считается неразобранным.
        status = DocStatus.ERROR
    elif notes:
        status = DocStatus.FLAGGED
    else:
        status = DocStatus.DONE

    return ValidationReport(
        status=status,
        fields=checks,
        missing_required=missing,
        notes=notes,
    )


def missing_reason(doc_type: DocType, missing: list[str]) -> str:
    """Текст причины для `Document.error_reason` — его увидит и соискатель."""
    titles = ", ".join(_title(name) for name in missing) or "обязательные поля"
    return f"Не удалось распознать: {titles}. Загрузите более качественное фото."
