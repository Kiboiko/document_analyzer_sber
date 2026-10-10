"""Сопоставление ФИО и ключевых дат между документами заявки (TZ 8, бонус).

Задача: поймать, что паспорт и СНИЛС принадлежат разным людям, но не поднимать
панику из-за того, что OCR прочитал «Иванов» как «Иваноб», а в eu_id ФИО
написано латиницей.

Поэтому сравнение трёхступенчатое: нормализация -> транслитерация к одному
алфавиту -> нечёткое сравнение с порогом.
"""

from __future__ import annotations

import logging
import re
import uuid
from difflib import SequenceMatcher
from itertools import permutations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    CrossCheckKind,
    CrossCheckStatus,
    DocType,
    EventType,
)
from app.models import CrossCheck, Document, Field
from app.services import history

log = logging.getLogger(__name__)

__all__ = ["normalize_fio", "translit", "similarity", "run_for_application"]

#: Ниже этого сходства считаем, что это разные люди, а не опечатка OCR.
FIO_THRESHOLD = 0.82

#: Больше четырёх слов в ФИО не бывает; остальное — мусор распознавания.
_MAX_NAME_PARTS = 4

_WS_RE = re.compile(r"\s+")
_NON_NAME_RE = re.compile(r"[^a-zа-яё\s]", re.IGNORECASE)

#: Кириллица -> латиница. Многобуквенные сочетания идут первыми, иначе
#: «щ» превратится в «sh»+«ch» по частям.
_TRANSLIT = (
    ("щ", "sh"), ("ш", "sh"), ("ч", "ch"), ("ж", "zh"), ("ц", "c"),
    ("ю", "u"), ("я", "a"), ("ё", "e"), ("й", "i"), ("ы", "i"),
    ("э", "e"), ("х", "h"), ("ъ", ""), ("ь", ""),
    ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("д", "d"),
    ("е", "e"), ("з", "z"), ("и", "i"), ("к", "k"), ("л", "l"),
    ("м", "m"), ("н", "n"), ("о", "o"), ("п", "p"), ("р", "r"),
    ("с", "s"), ("т", "t"), ("у", "u"), ("ф", "f"),
)

#: Разные алфавиты пишут одни и те же звуки по-разному: KARIMOV/CARIMOV,
#: IVANOV/IWANOW. Сводим к одному варианту уже после транслитерации.
_LATIN_FOLD = (
    ("kh", "h"), ("ck", "k"), ("ph", "f"), ("ee", "i"), ("oo", "u"),
    ("yi", "i"), ("ij", "i"), ("w", "v"), ("y", "i"), ("j", "i"),
    ("q", "k"), ("x", "ks"),
)


def normalize_fio(value: str) -> str:
    """Регистр, пробелы, ё/е, дефисы — к одному виду."""
    text = (value or "").lower().replace("ё", "е")
    text = text.replace("-", " ").replace("'", "").replace("’", "")
    text = _NON_NAME_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def translit(value: str) -> str:
    """Кириллица -> латиница, чтобы сравнивать паспорт РФ и eu_id."""
    text = normalize_fio(value)
    for src, dst in _TRANSLIT:
        text = text.replace(src, dst)
    for src, dst in _LATIN_FOLD:
        text = text.replace(src, dst)
    return _WS_RE.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    """Нечёткое сходство 0..1 (опечатки распознавания — не расхождение).

    Сравниваем по словам, а не по строке целиком: в одном документе отчества
    может не быть вовсе, и это не повод объявлять расхождение.

    Слова сопоставляются один к одному — каждое слово из короткого набора
    занимает своё слово в длинном. Иначе «Иванов Иван» и «Сидоров Иван»
    получили бы высокое сходство: фамилия «Иванов» нашла бы себе пару в имени
    «Иван», а настоящая разница фамилий осталась бы незамеченной.
    """
    left, right = translit(a), translit(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    short, long = sorted((left.split(), right.split()), key=len)
    # ФИО — это 2-4 слова, перестановок единицы; ограничение на всякий случай,
    # чтобы мусор от OCR не превратился в факториал.
    short, long = short[:_MAX_NAME_PARTS], long[:_MAX_NAME_PARTS]

    best = 0.0
    for variant in permutations(long, len(short)):
        score = sum(
            SequenceMatcher(None, word, other).ratio()
            for word, other in zip(short, variant)
        ) / len(short)
        best = max(best, score)
    return best


async def run_for_application(
    session: AsyncSession,
    application_id: uuid.UUID,
    actor: str = history.ACTOR_WORKER,
) -> list[CrossCheck]:
    """Сверить ФИО и дату рождения по всем документам заявки.

    Расхождение -> CrossCheck(mismatch) + событие + замечание в карточке.
    Коммит — за вызывающим.
    """
    values = await _collect(session, application_id, ("full_name", "birth_date"))

    results = [
        await _upsert(
            session,
            application_id,
            CrossCheckKind.FIO,
            *_compare_fio(values.get("full_name", [])),
            actor=actor,
        ),
        await _upsert(
            session,
            application_id,
            CrossCheckKind.DATE,
            *_compare_dates(values.get("birth_date", [])),
            actor=actor,
        ),
    ]
    return [row for row in results if row is not None]


# --------------------------------------------------------------------------- #
# Внутреннее
# --------------------------------------------------------------------------- #


def _compare_fio(
    entries: list[tuple[DocType, str]],
) -> tuple[CrossCheckStatus | None, str | None]:
    """Сравнить ФИО между документами. None — сравнивать нечего."""
    if len(entries) < 2:
        return None, None

    worst = 1.0
    worst_pair: tuple[tuple[DocType, str], tuple[DocType, str]] | None = None

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            score = similarity(entries[i][1], entries[j][1])
            if score < worst:
                worst = score
                worst_pair = (entries[i], entries[j])

    if worst >= FIO_THRESHOLD or worst_pair is None:
        shown = ", ".join(f"{doc.value}: {value}" for doc, value in entries)
        return CrossCheckStatus.MATCH, f"ФИО совпадает ({shown})"

    (left_doc, left), (right_doc, right) = worst_pair
    return CrossCheckStatus.MISMATCH, (
        f"ФИО расходится: «{left}» ({left_doc.value}) "
        f"и «{right}» ({right_doc.value}), сходство {worst:.0%}"
    )


def _compare_dates(
    entries: list[tuple[DocType, str]],
) -> tuple[CrossCheckStatus | None, str | None]:
    """Сравнить даты рождения. Даты уже нормализованы моделью к ДД.ММ.ГГГГ."""
    if len(entries) < 2:
        return None, None

    distinct = {value for _, value in entries}
    if len(distinct) == 1:
        return CrossCheckStatus.MATCH, f"Дата рождения совпадает ({distinct.pop()})"

    shown = ", ".join(f"{doc.value}: {value}" for doc, value in entries)
    return CrossCheckStatus.MISMATCH, f"Дата рождения расходится — {shown}"


async def _collect(
    session: AsyncSession,
    application_id: uuid.UUID,
    names: tuple[str, ...],
) -> dict[str, list[tuple[DocType, str]]]:
    """Собрать значения интересующих полей по всем документам заявки."""
    rows = (
        await session.execute(
            select(Document.doc_type, Field.name, Field.value)
            .join(Field, Field.document_id == Document.id)
            .where(
                Document.application_id == application_id,
                Field.name.in_(names),
                Field.value.isnot(None),
            )
        )
    ).all()

    collected: dict[str, list[tuple[DocType, str]]] = {}
    for doc_type, name, value in rows:
        if value and value.strip():
            collected.setdefault(name, []).append((DocType(doc_type), value.strip()))
    return collected


async def _upsert(
    session: AsyncSession,
    application_id: uuid.UUID,
    kind: CrossCheckKind,
    status: CrossCheckStatus | None,
    detail: str | None,
    actor: str,
) -> CrossCheck | None:
    """Записать результат сверки. На заявку — одна запись каждого вида."""
    if status is None:
        # Сравнивать пока нечего: одного документа для сверки недостаточно.
        return None

    existing = await session.scalar(
        select(CrossCheck)
        .where(
            CrossCheck.application_id == application_id,
            CrossCheck.kind == kind,
        )
        .limit(1)
    )

    was_mismatch = existing is not None and existing.status == CrossCheckStatus.MISMATCH

    if existing is None:
        existing = CrossCheck(
            application_id=application_id, kind=kind, status=status, detail=detail
        )
        session.add(existing)
    else:
        existing.status = status
        existing.detail = detail

    await session.flush()

    # Событие пишем только когда расхождение появилось, а не на каждый разбор:
    # иначе таймлайн заполнится одинаковыми строками.
    if status == CrossCheckStatus.MISMATCH and not was_mismatch:
        await history.record(
            session,
            application_id,
            EventType.DOCUMENT_FLAGGED,
            actor,
            payload={"crosscheck": kind.value, "detail": detail},
        )
        log.info("расхождение %s в заявке %s: %s", kind.value, application_id, detail)

    return existing
