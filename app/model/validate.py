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
import unicodedata
from datetime import date

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

# Разделитель частей даты: точка, дефис, косая или пробел.
_SEP = r"[.\-/\s]+"
_NUM_DATE_RE = re.compile(rf"^(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{4}}|\d{{2}})$")
_ISO_DATE_RE = re.compile(rf"^(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})$")
# «01 JAN 1990», «1 января 1990», «01-ЯНВ-90»: месяц словом, любым алфавитом.
_NAMED_DATE_RE = re.compile(
    rf"^(\d{{1,2}}){_SEP}?([^\W\d_]{{3,}})\.?{_SEP}?(\d{{4}}|\d{{2}})$"
)

#: Месяц по первым трём буквам: английские сокращения и русские названия
#: («янв», «января», «январь» — всё начинается на «янв»).
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}

_WS_RE = re.compile(r"\s+")
#: Знаки, допустимые в имени латиницей рядом с буквами.
_NAME_PUNCT = frozenset(" '’`-.,/")

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

    try:
        if match := _ISO_DATE_RE.match(text):
            year, month, day = (int(part) for part in match.groups())
        elif match := _NUM_DATE_RE.match(text):
            day, month, year = (int(part) for part in match.groups())
            year = _full_year(year, match.group(3))
        elif match := _NAMED_DATE_RE.match(text):
            day = int(match.group(1))
            month = _MONTHS.get(match.group(2)[:3].lower())
            if month is None:
                return None
            year = _full_year(int(match.group(3)), match.group(3))
        else:
            return None
        return date(year, month, day)
    except ValueError:
        # 31.02.1990 и подобное: такой даты не существует.
        return None


def _full_year(year: int, raw: str) -> int:
    """Двузначный год -> четырёхзначный (00-68 это 2000-е, как у strptime %y).

    Век тут угадывается, и для даты рождения угадать можно неверно; такой
    документ уйдёт оператору с пометкой, а не будет молча принят.
    """
    if len(raw) == 4:
        return year
    return 2000 + year if year <= 68 else 1900 + year


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
    result = {
        name: normalize_field(doc_type, name, value) for name, value in fields.items()
    }
    if doc_type is DocType.PASSPORT_RF:
        _split_rf_series_number(result)
    return result


def _split_rf_series_number(fields: dict[str, str | None]) -> None:
    """Серия и номер паспорта РФ напечатаны слитно: «45 04 123456».

    Модель часто возвращает их одним значением, и тогда по формальным правилам
    серия не нашлась, а номер получился из 10 цифр, хотя документ прочитан
    полностью. Разделяем только когда это однозначно: ровно 10 цифр
    (4 серии + 6 номера); при расхождении с уже найденной серией ничего не
    трогаем, пусть разбирается проверка.
    """
    series = re.sub(r"\D", "", fields.get("series") or "")
    number = re.sub(r"\D", "", fields.get("number") or "")

    if len(number) == 10 and (not series or number.startswith(series) and len(series) == 4):
        fields["series"], fields["number"] = number[:4], number[4:]
    elif len(series) == 10 and not number:
        fields["series"], fields["number"] = series[:4], series[4:]


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


def _is_latin_name(value: str) -> bool:
    """Имя целиком латиницей, включая диакритику (JOSÉ, ŁUKASZ, ZOË, ŞAHIN).

    Диапазон A-Z здесь не годится: в удостоверениях ЕС диакритика обычное дело,
    и каждое такое имя ушло бы в замечание зря. Проверяем, что каждая буква
    принадлежит латинскому письму, а не кириллице, греческому или арабскому.
    """
    letters = 0
    for char in value:
        if char.isalpha():
            if "LATIN" not in unicodedata.name(char, ""):
                return False
            letters += 1
        elif char not in _NAME_PUNCT:
            return False
    return letters > 0


def _check_latin(name: str, value: str) -> FieldValidation:
    if not _is_latin_name(value):
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
