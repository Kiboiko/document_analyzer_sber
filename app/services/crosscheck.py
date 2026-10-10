"""Сопоставление ФИО и ключевых дат между документами заявки (TZ 8, бонус).

Владелец: Степан (ветка feat/model-intake).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CrossCheck


def normalize_fio(value: str) -> str:
    """Регистр, пробелы, ё/е, дефисы — к одному виду."""
    raise NotImplementedError  # TODO


def translit(value: str) -> str:
    """Кириллица <-> латиница, чтобы сравнивать паспорт РФ и eu_id."""
    raise NotImplementedError  # TODO


def similarity(a: str, b: str) -> float:
    """Нечёткое сходство 0..1 (опечатки распознавания — не расхождение)."""
    raise NotImplementedError  # TODO


async def run_for_application(
    session: AsyncSession,
    application_id: uuid.UUID,
) -> list[CrossCheck]:
    """Сверить ФИО и дату рождения по всем документам заявки.

    Расхождение -> CrossCheck(mismatch) + событие + замечание в карточке.
    """
    raise NotImplementedError  # TODO
