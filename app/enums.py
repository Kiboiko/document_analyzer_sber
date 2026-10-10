"""Перечисления и справочные таблицы предметной области.

Единственный источник истины по константам (TZ.md, разделы 2–3, 4, 9).
Только данные и перечисления: никакой логики валидации — она в `app/model/validate.py`.
"""

from __future__ import annotations

from enum import StrEnum

# --------------------------------------------------------------------------- #
# Страны и типы документов
# --------------------------------------------------------------------------- #


class Country(StrEnum):
    """Страна соискателя. Определяет обязательный набор документов (TZ 2)."""

    RU = "ru"          # Россия
    UZ = "uz"          # Узбекистан
    EU = "eu"          # Европа (ЕС)


class DocType(StrEnum):
    """Восемь типов документов (TZ 3)."""

    PASSPORT_RF = "passport_rf"
    SNILS = "snils"
    INN = "inn"
    PASSPORT_UZ = "passport_uz"
    MIGRATION_CARD = "migration_card"
    PATENT = "patent"
    EU_ID = "eu_id"
    RESIDENCE_PERMIT = "residence_permit"


#: Обязательный набор документов по стране (TZ 2).
#: Страница соискателя подстраивается под выбранную страну.
REQUIRED_DOCUMENTS: dict[Country, tuple[DocType, ...]] = {
    Country.RU: (DocType.PASSPORT_RF, DocType.SNILS, DocType.INN),
    Country.UZ: (DocType.PASSPORT_UZ, DocType.MIGRATION_CARD, DocType.PATENT),
    Country.EU: (DocType.EU_ID, DocType.RESIDENCE_PERMIT),
}

#: Человекочитаемые названия для UI.
COUNTRY_TITLES: dict[Country, str] = {
    Country.RU: "Россия",
    Country.UZ: "Узбекистан",
    Country.EU: "Европа (ЕС)",
}

DOC_TYPE_TITLES: dict[DocType, str] = {
    DocType.PASSPORT_RF: "Паспорт РФ",
    DocType.SNILS: "СНИЛС",
    DocType.INN: "ИНН",
    DocType.PASSPORT_UZ: "Паспорт гражданина Узбекистана",
    DocType.MIGRATION_CARD: "Миграционная карта",
    DocType.PATENT: "Патент на работу",
    DocType.EU_ID: "Удостоверение личности / загранпаспорт ЕС",
    DocType.RESIDENCE_PERMIT: "Вид на жительство",
}


# --------------------------------------------------------------------------- #
# Поля документов (TZ 3)
# --------------------------------------------------------------------------- #

#: Канонические имена извлекаемых полей. Общий контракт модели, БД и UI:
#: `Field.name` принимает только значения отсюда.
EXTRACTED_FIELDS: dict[DocType, tuple[str, ...]] = {
    DocType.PASSPORT_RF: (
        "series", "number", "full_name", "birth_date",
        "birth_place", "issued_by", "issue_date",
    ),
    DocType.SNILS: ("number", "full_name"),
    DocType.INN: ("number",),
    DocType.PASSPORT_UZ: ("number", "full_name", "birth_date", "expiry_date"),
    DocType.MIGRATION_CARD: ("number", "entry_date", "valid_until"),
    DocType.PATENT: ("number", "issue_date", "valid_until", "region"),
    DocType.EU_ID: ("number", "full_name", "birth_date", "citizenship", "expiry_date"),
    DocType.RESIDENCE_PERMIT: ("number", "issue_date", "expiry_date", "permit_type"),
}

#: Минимальный набор: без этих полей документ считается неразобранным (status=error).
MIN_FIELDS: dict[DocType, tuple[str, ...]] = {
    DocType.PASSPORT_RF: ("series", "number", "full_name", "birth_date"),
    DocType.SNILS: ("number",),
    DocType.INN: ("number",),
    DocType.PASSPORT_UZ: ("number", "full_name"),
    DocType.MIGRATION_CARD: ("number",),
    DocType.PATENT: ("number", "valid_until"),
    DocType.EU_ID: ("number", "full_name"),
    DocType.RESIDENCE_PERMIT: ("number", "expiry_date"),
}

#: Подписи полей для UI оператора и соискателя.
FIELD_TITLES: dict[str, str] = {
    "series": "Серия",
    "number": "Номер",
    "full_name": "ФИО",
    "birth_date": "Дата рождения",
    "birth_place": "Место рождения",
    "issued_by": "Кем выдан",
    "issue_date": "Дата выдачи",
    "expiry_date": "Срок действия",
    "valid_until": "Действует до",
    "entry_date": "Дата въезда",
    "region": "Регион",
    "citizenship": "Гражданство",
    "permit_type": "Тип разрешения",
}


# --------------------------------------------------------------------------- #
# Статусы
# --------------------------------------------------------------------------- #


class DocStatus(StrEnum):
    """Статус документа (TZ 3).

    accepted -> processing -> done | error | flagged.
    `flagged` = разобран, но есть замечание (просрочка, расхождение, формат).
    Просрочка документа — не ошибка приёма.
    """

    ACCEPTED = "accepted"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"
    FLAGGED = "flagged"


class ApplicationStatus(StrEnum):
    """Агрегированный статус заявки — производное от статусов её документов.

    Набор значений — черновик (TZ 5: «финально — команда»), используется
    для фильтров в кабинете оператора.
    """

    CREATED = "created"        # заявка создана, документов ещё нет
    PROCESSING = "processing"  # есть документы в приёме/разборе
    DONE = "done"             # все документы разобраны


class JobStatus(StrEnum):
    """Статус фоновой задачи разбора (TZ 4, 6)."""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class FieldSource(StrEnum):
    """Источник значения поля: модель или правка оператора (TZ 4)."""

    MODEL = "model"
    OPERATOR = "operator"


class EventType(StrEnum):
    """Типы событий таймлайна заявки (TZ 9)."""

    APPLICATION_CREATED = "application_created"
    FILES_UPLOADED = "files_uploaded"
    FILE_ACCEPTED = "file_accepted"
    FILE_REJECTED = "file_rejected"
    PARSE_STARTED = "parse_started"
    PARSE_DONE = "parse_done"
    PARSE_FAILED = "parse_failed"
    FIELD_CORRECTED = "field_corrected"
    REPARSE_REQUESTED = "reparse_requested"
    DOCUMENT_FLAGGED = "document_flagged"


class CrossCheckKind(StrEnum):
    """Что сопоставляем между документами заявки (TZ 8)."""

    FIO = "fio"
    DATE = "date"


class CrossCheckStatus(StrEnum):
    """Результат сопоставления (TZ 8)."""

    MATCH = "match"
    MISMATCH = "mismatch"


class Role(StrEnum):
    """Роли Keycloak (TZ 1)."""

    ADMIN = "admin"
    OPERATOR = "operator"
