"""Правила проверки полей (ТЗ, раздел 3) на всех восьми типах документов.

Дата «сегодня» фиксирована, чтобы проверки просрочки не меняли исход со
временем.
"""

import sys
from datetime import date

from app.enums import DocType as D
from app.model.validate import normalize_fields, parse_date, validate_document

TODAY = date(2026, 10, 10)

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


def run(doc_type, raw, expect_status, expect_fields=None, expect_note=None, title=""):
    """Прогнать нормализацию + проверку и сверить с ожиданием."""
    fields = normalize_fields(doc_type, raw)
    report = validate_document(doc_type, fields, today=TODAY)

    check(
        f"{title}: статус {expect_status}",
        report.status.value == expect_status,
        f"получено {report.status.value}, замечания {report.notes}",
    )

    for name, expected in (expect_fields or {}).items():
        check(
            f"{title}: {name} -> {expected!r}",
            fields.get(name) == expected,
            f"получено {fields.get(name)!r}",
        )

    if expect_note:
        check(
            f"{title}: есть замечание про «{expect_note}»",
            any(expect_note in note for note in report.notes),
            str(report.notes),
        )


print("\n1. Паспорт РФ")
run(D.PASSPORT_RF,
    {"series": "45 04", "number": "123 456", "full_name": "ИВАНОВ  ИВАН\nИВАНОВИЧ",
     "birth_date": "01.01.1990 г.", "birth_place": "г. Москва",
     "issued_by": "ОВД Басманный", "issue_date": "2010-05-20"},
    "done",
    {"series": "4504", "number": "123456", "birth_date": "01.01.1990",
     "issue_date": "20.05.2010", "full_name": "ИВАНОВ ИВАН ИВАНОВИЧ"},
    title="чистый паспорт")

run(D.PASSPORT_RF,
    {"series": "450", "number": "123456", "full_name": "Иванов Иван",
     "birth_date": "01.01.2030"},
    "flagged", expect_note="4 цифр", title="серия из 3 цифр и дата рождения в будущем")

run(D.PASSPORT_RF,
    {"series": "4504", "number": None, "full_name": "Иванов Иван",
     "birth_date": "01.01.1990"},
    "error", title="нет номера — минимум не собран")

print("\n2. СНИЛС и ИНН")
run(D.SNILS, {"number": "112 233 445 95", "full_name": "Иванов Иван Иванович"},
    "done", {"number": "112-233-445 95"}, title="СНИЛС приведён к формату")
run(D.SNILS, {"number": "11223344", "full_name": "Иванов Иван"},
    "flagged", expect_note="11 цифр", title="СНИЛС короче 11 цифр")
run(D.INN, {"number": "7707 0834 9391"}, "done", {"number": "770708349391"},
    title="ИНН из 12 цифр")
run(D.INN, {"number": "770708349"}, "flagged", expect_note="12 цифр",
    title="ИНН короче 12 цифр")
run(D.INN, {"number": None}, "error", title="ИНН не распознан")

print("\n3. Узбекистан")
run(D.PASSPORT_UZ,
    {"number": "AA1234567", "full_name": "KARIMOV AZIZ", "birth_date": "05.03.1988",
     "expiry_date": "01.01.2030"},
    "done", title="действующий паспорт УЗ")
run(D.PASSPORT_UZ,
    {"number": "AA1234567", "full_name": "KARIMOV AZIZ", "birth_date": "05.03.1988",
     "expiry_date": "01.01.2020"},
    "flagged", expect_note="срок действия истёк",
    title="просроченный паспорт — принимаем, но помечаем")
run(D.MIGRATION_CARD, {"number": "4521 0099887", "entry_date": "01.03.2026",
                       "valid_until": "01.06.2026"},
    "done", title="миграционная карта")
run(D.MIGRATION_CARD, {"number": "4521 0099887", "entry_date": "01.01.2027",
                       "valid_until": "01.04.2027"},
    "flagged", expect_note="дата в будущем", title="въезд в будущем")
run(D.PATENT, {"number": "77 2024 0012345", "issue_date": "01.02.2025",
               "valid_until": "01.02.2027", "region": "Москва"},
    "done", title="действующий патент")
run(D.PATENT, {"number": "77 2024 0012345", "issue_date": "01.02.2023",
               "valid_until": "01.02.2024", "region": "Москва"},
    "flagged", expect_note="срок действия истёк", title="просроченный патент")
run(D.PATENT, {"number": "77 2024 0012345", "valid_until": None},
    "error", title="у патента нет срока — минимум не собран")

print("\n4. Европа")
run(D.EU_ID, {"number": "X1234567", "full_name": "JAN KOWALSKI",
              "birth_date": "01.01.1990", "citizenship": "POL",
              "expiry_date": "01.01.2030"},
    "done", title="удостоверение ЕС латиницей")
run(D.EU_ID, {"number": "X1234567", "full_name": "Иванов Иван",
              "birth_date": "01.01.1990", "citizenship": "POL",
              "expiry_date": "01.01.2030"},
    "flagged", expect_note="латиница", title="ФИО кириллицей в документе ЕС")
EU_BASE = {"number": "X1234567", "full_name": "JAN KOWALSKI",
           "birth_date": "01.01.1990", "expiry_date": "01.01.2030"}
for citizenship in ("POL", "Polska", "Republic of Poland", "DEUTSCH", "D", "DE",
                    "Française", "Česká republika", "Ελληνική", "Österreich",
                    "Германия"):
    run(D.EU_ID, {**EU_BASE, "citizenship": citizenship},
        "done", title=f"гражданство ЕС: {citizenship}")
for citizenship in ("BLR", "Republic of Belarus", "Республика Беларусь", "IND",
                    "Indian", "Republic of India", "UZB", "GBR", "Swiss", "Norway"):
    run(D.EU_ID, {**EU_BASE, "citizenship": citizenship},
        "flagged", expect_note="не входит в ЕС", title=f"гражданство не ЕС: {citizenship}")
run(D.RESIDENCE_PERMIT, {"number": "82 1234567", "issue_date": "10.10.2021",
                         "expiry_date": "01.01.2030", "permit_type": "временное"},
    "done", title="действующий ВНЖ")
run(D.RESIDENCE_PERMIT, {"number": "82 1234567", "issue_date": "10.10.2021",
                         "expiry_date": "бессрочно", "permit_type": "постоянное"},
    "done", title="бессрочный ВНЖ — не ошибка, хотя срок в минимуме")
run(D.RESIDENCE_PERMIT, {"number": "82 1234567", "issue_date": "10.10.2015",
                         "expiry_date": "10.10.2020", "permit_type": "временное"},
    "flagged", expect_note="срок действия истёк", title="просроченный ВНЖ")

print("\n5. Разбор даты")
for raw, expected in [("01.01.1990", date(1990, 1, 1)), ("1990-01-01", date(1990, 1, 1)),
                      ("31/12/2025", date(2025, 12, 31)), ("01-01-1990", date(1990, 1, 1)),
                      ("мусор", None), ("", None), (None, None)]:
    check(f"parse_date({raw!r}) -> {expected}", parse_date(raw) == expected,
          str(parse_date(raw)))

print("\n6. Даты: название месяца, пробелы, разные разделители")
for raw, expected in [("01 JAN 1990", date(1990, 1, 1)), ("1 Jan 1990", date(1990, 1, 1)),
                      ("01 ЯНВ 1990", date(1990, 1, 1)), ("1 января 1990 г.", date(1990, 1, 1)),
                      ("05 03 1988", date(1988, 3, 5)), ("5.3.1988", date(1988, 3, 5)),
                      ("1990/01/01", date(1990, 1, 1)), ("01-JAN-90", date(1990, 1, 1)),
                      ("31.02.1990", None), ("1990-13-01", None), ("12 foo 1990", None)]:
    check(f"parse_date({raw!r}) -> {expected}", parse_date(raw) == expected,
          str(parse_date(raw)))

run(D.EU_ID, {"number": "X1", "full_name": "JAN KOWALSKI", "birth_date": "01 JAN 1990",
              "expiry_date": "15 MAR 2030"},
    "done", {"birth_date": "01.01.1990", "expiry_date": "15.03.2030"},
    title="дата словом приводится к ДД.ММ.ГГГГ")

print("\n7. Латиница с диакритикой — не замечание")
for name in ["JOSÉ MÜLLER", "ŁUKASZ ZAŁĘSKI", "ZOË O'BRIEN-SMITH", "NOVÁK, JAN", "ŞAHIN ÇAĞLA"]:
    run(D.EU_ID, {"number": "X1", "full_name": name, "expiry_date": "01.01.2030"},
        "done", title=f"ЕС: {name}")
for name in ["Иванов Иван", "IVANOV ИВАН", "ΓΙΩΡΓΟΣ ΠΑΠΑ", "JAN 2ND"]:
    run(D.EU_ID, {"number": "X1", "full_name": name, "expiry_date": "01.01.2030"},
        "flagged", expect_note="латиница", title=f"ЕС: не латиница «{name}»")

print("\n8. Серия и номер паспорта РФ слитно")
base = {"full_name": "Иванов Иван", "birth_date": "01.01.1990"}
for title, raw, series, number, status in [
    ("всё в номере", {"series": None, "number": "4504 123456"}, "4504", "123456", "done"),
    ("серия повторена в номере", {"series": "4504", "number": "4504123456"}, "4504", "123456", "done"),
    ("всё в серии", {"series": "45 04 123456", "number": None}, "4504", "123456", "done"),
    ("раздельно, как надо", {"series": "4504", "number": "123456"}, "4504", "123456", "done"),
    # Серия не совпадает с началом номера: неоднозначно, ничего не режем.
    ("серия противоречит номеру", {"series": "4599", "number": "4504123456"}, "4599", "4504123456", "flagged"),
    ("короткая серия", {"series": "45", "number": "4504123456"}, "45", "4504123456", "flagged"),
]:
    run(D.PASSPORT_RF, {**base, **raw}, status, {"series": series, "number": number}, title=title)

print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
if FAIL:
    print("ПРОВАЛЫ:", FAIL)
sys.exit(1 if FAIL else 0)
