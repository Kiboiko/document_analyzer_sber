"""Сопоставление ФИО и дат против живой БД: записи, события, повторный прогон."""

import asyncio
import sys

from sqlalchemy import select, text

from app.db import create_all, engine, session_factory
from app.enums import (
    ApplicationStatus,
    Country,
    CrossCheckKind,
    CrossCheckStatus,
    DocStatus,
    DocType,
    FieldSource,
)
from app.models import Application, CrossCheck, Document, Event, Field
from app.services import crosscheck

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


async def seed(session, docs):
    """docs: [(DocType, {поле: значение})] -> заявка с документами и полями."""
    application = Application(country=Country.RU, status=ApplicationStatus.PROCESSING)
    session.add(application)
    await session.flush()

    for doc_type, fields in docs:
        document = Document(
            application_id=application.id, doc_type=doc_type, status=DocStatus.DONE
        )
        session.add(document)
        await session.flush()
        for name, value in fields.items():
            session.add(
                Field(
                    document_id=document.id,
                    name=name,
                    value=value,
                    source=FieldSource.MODEL,
                    is_valid=True,
                )
            )
    await session.flush()
    return application


async def main():
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await create_all()

    print("\n1. Один документ — сверять нечего")
    async with session_factory() as s:
        app1 = await seed(s, [(DocType.PASSPORT_RF, {"full_name": "ИВАНОВ ИВАН", "birth_date": "01.01.1990"})])
        rows = await crosscheck.run_for_application(s, app1.id)
        await s.commit()
        check("по одному документу записей сверки нет", rows == [], str(rows))

    print("\n2. ФИО и дата совпадают — match")
    async with session_factory() as s:
        app2 = await seed(s, [
            (DocType.PASSPORT_RF, {"full_name": "ИВАНОВ ИВАН ИВАНОВИЧ", "birth_date": "01.01.1990"}),
            (DocType.SNILS, {"full_name": "Иванов Иван Иванович"}),
            (DocType.INN, {"number": "770708349391"}),
        ])
        rows = await crosscheck.run_for_application(s, app2.id)
        await s.commit()
        fio = next(r for r in rows if r.kind == CrossCheckKind.FIO)
        check("ФИО в разном регистре — совпадение",
              fio.status == CrossCheckStatus.MATCH, f"{fio.status} {fio.detail}")
        check("дата рождения в одном документе — сверять нечего",
              not any(r.kind == CrossCheckKind.DATE for r in rows))

    print("\n3. Разные люди — mismatch + событие")
    async with session_factory() as s:
        app3 = await seed(s, [
            (DocType.PASSPORT_RF, {"full_name": "ИВАНОВ ИВАН ИВАНОВИЧ", "birth_date": "01.01.1990"}),
            (DocType.SNILS, {"full_name": "ПЕТРОВ ПЁТР ПЕТРОВИЧ", "birth_date": "02.02.1992"}),
        ])
        rows = await crosscheck.run_for_application(s, app3.id)
        await s.commit()

        fio = next(r for r in rows if r.kind == CrossCheckKind.FIO)
        dat = next(r for r in rows if r.kind == CrossCheckKind.DATE)
        check("ФИО расходится", fio.status == CrossCheckStatus.MISMATCH, fio.detail)
        check("в пояснении видно оба значения и документы",
              "ИВАНОВ" in fio.detail and "ПЕТРОВ" in fio.detail and "snils" in fio.detail,
              fio.detail)
        check("дата рождения расходится", dat.status == CrossCheckStatus.MISMATCH, dat.detail)

        events = (await s.scalars(
            select(Event).where(Event.application_id == app3.id)
        )).all()
        flags = [e for e in events if e.type.value == "document_flagged"]
        check("на каждое расхождение записано событие", len(flags) == 2,
              f"событий={len(flags)}")
        check("в событии указан вид сверки",
              {e.payload.get("crosscheck") for e in flags} == {"fio", "date"},
              str([e.payload for e in flags]))

    print("\n4. Повторный прогон: без дублей записей и событий")
    async with session_factory() as s:
        await crosscheck.run_for_application(s, app3.id)
        await s.commit()
        rows = (await s.scalars(
            select(CrossCheck).where(CrossCheck.application_id == app3.id)
        )).all()
        check("на заявку по одной записи каждого вида", len(rows) == 2, f"записей={len(rows)}")
        events = (await s.scalars(
            select(Event).where(Event.application_id == app3.id)
        )).all()
        flags = [e for e in events if e.type.value == "document_flagged"]
        check("повторное расхождение не дублирует событие", len(flags) == 2,
              f"событий={len(flags)}")

    print("\n5. Оператор исправил ФИО — расхождение снимается")
    async with session_factory() as s:
        doc = await s.scalar(
            select(Document).where(
                Document.application_id == app3.id,
                Document.doc_type == DocType.SNILS,
            )
        )
        field = await s.scalar(
            select(Field).where(Field.document_id == doc.id, Field.name == "full_name")
        )
        field.value = "Иванов Иван Иванович"
        field.source = FieldSource.OPERATOR
        await s.flush()

        rows = await crosscheck.run_for_application(s, app3.id)
        await s.commit()
        fio = next(r for r in rows if r.kind == CrossCheckKind.FIO)
        check("после правки ФИО — match", fio.status == CrossCheckStatus.MATCH, fio.detail)

    print("\n6. Латиница против кириллицы — не расхождение")
    async with session_factory() as s:
        app6 = await seed(s, [
            (DocType.PASSPORT_RF, {"full_name": "КАРИМОВ АЗИЗ", "birth_date": "05.03.1988"}),
            (DocType.EU_ID, {"full_name": "KARIMOV AZIZ", "birth_date": "05.03.1988"}),
        ])
        rows = await crosscheck.run_for_application(s, app6.id)
        await s.commit()
        fio = next(r for r in rows if r.kind == CrossCheckKind.FIO)
        check("транслит сведён к одному алфавиту",
              fio.status == CrossCheckStatus.MATCH, fio.detail)

    await engine.dispose()
    print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
