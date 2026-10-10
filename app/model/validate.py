"""Правила проверки полей по типу документа (TZ 3).

Просрочка — не ошибка приёма: документ помечается `flagged`.
Отсутствие поля из `MIN_FIELDS` — `error` (документ не разобран).

Владелец: Степан. Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

from app.enums import DocType
from app.schemas import FieldValidation, ValidationReport

__all__ = ["validate_field", "validate_document"]


def validate_field(doc_type: DocType, name: str, value: str | None) -> FieldValidation:
    """Проверить одно поле правилами своего типа документа."""
    raise NotImplementedError  # TODO(Степан)


def validate_document(
    doc_type: DocType,
    fields: dict[str, str | None],
) -> ValidationReport:
    """Собрать отчёт: статус документа, замечания по полям, нехватка минимума."""
    raise NotImplementedError  # TODO(Степан)
