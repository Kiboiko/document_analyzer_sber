"""Сквозной тест приёма: заявка -> загрузка -> статус -> очередь -> события.

Модель не вызывается: приём по ТЗ не должен её ждать.
"""

import asyncio
import io
import sys

import httpx
from PIL import Image
from sqlalchemy import select, text

from _safety import require_throwaway_db

require_throwaway_db()

from app.db import create_all, engine, session_factory
from app.main import app
from app.models import Document, Event, File, Job


def jpeg(color, size=(600, 400)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


OK = []
FAIL = []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


async def main():
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await create_all()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        print("\n1. Создание заявки")
        r = await c.post("/api/applications", json={"country": "ru"})
        check("POST /api/applications -> 200", r.status_code == 200, r.text[:200])
        data = r.json()
        app_id, token = data["id"], data["applicant_token"]
        check("выдан applicant_token", bool(token))
        check(
            "набор документов для РФ",
            data["required_documents"] == ["passport_rf", "snils", "inn"],
            str(data["required_documents"]),
        )

        print("\n2. Доступ по токену проверяется на сервере")
        r = await c.get(f"/api/applications/{app_id}/status",
                        params={"applicant_token": "00000000-0000-0000-0000-000000000000"})
        check("чужой токен -> 404", r.status_code == 404, str(r.status_code))
        r = await c.get(f"/api/applications/{app_id}/status", params={"applicant_token": token})
        check("свой токен -> 200", r.status_code == 200, str(r.status_code))

        print("\n3. Приём файла")
        r = await c.post(
            f"/api/applications/{app_id}/documents",
            params={"applicant_token": token},
            data={"doc_type": "passport_rf"},
            files=[("files", ("passport.jpg", jpeg("white"), "image/jpeg"))],
        )
        check("загрузка -> 200", r.status_code == 200, r.text[:300])
        up = r.json()
        doc_id = up["document_id"]
        check("файл принят", len(up["accepted"]) == 1, str(up))
        check("статус документа accepted", up["status"] == "accepted", up["status"])

        print("\n4. Дедуп повторной загрузки того же файла")
        r = await c.post(
            f"/api/applications/{app_id}/documents",
            params={"applicant_token": token},
            data={"doc_type": "passport_rf"},
            files=[("files", ("passport-copy.jpg", jpeg("white"), "image/jpeg"))],
        )
        up2 = r.json()
        check("дубль распознан, не принят повторно",
              len(up2["duplicates"]) == 1 and not up2["accepted"], str(up2))

        print("\n5. Отказы — на сервере, с понятной причиной")
        r = await c.post(
            f"/api/applications/{app_id}/documents",
            params={"applicant_token": token},
            data={"doc_type": "passport_rf"},
            files=[("files", ("virus.exe", b"MZ\x90\x00" + b"\x00" * 500, "image/jpeg"))],
        )
        up3 = r.json()
        check("не-картинка отклонена, несмотря на image/jpeg в заголовке",
              len(up3["rejected"]) == 1, str(up3["rejected"]))

        r = await c.post(
            f"/api/applications/{app_id}/documents",
            params={"applicant_token": token},
            data={"doc_type": "patent"},
            files=[("files", ("p.jpg", jpeg("red"), "image/jpeg"))],
        )
        check("тип документа не из набора страны -> 400", r.status_code == 400, r.text[:200])

        print("\n6. Очередь и журнал")
        async with session_factory() as s:
            jobs = (await s.scalars(select(Job))).all()
            check("на документ одна задача, не на файл", len(jobs) == 1, f"задач={len(jobs)}")
            check("задача в pending", jobs and jobs[0].status.value == "pending")

            files = (await s.scalars(select(File))).all()
            check("в БД один файл (дубль не записан)", len(files) == 1, f"файлов={len(files)}")

            docs = (await s.scalars(select(Document))).all()
            check("документ создан один на тип", len(docs) == 1, f"документов={len(docs)}")

            events = (await s.scalars(select(Event).order_by(Event.created_at))).all()
            types = [e.type.value for e in events]
            check("событие создания заявки", "application_created" in types, str(types))
            check("событие приёма файла", "file_accepted" in types)
            check("событие отказа с причиной", "file_rejected" in types)
            rejected = [e for e in events if e.type.value == "file_rejected"]
            check("в отказе записана причина",
                  bool(rejected and rejected[0].payload.get("reason")),
                  str(rejected[0].payload) if rejected else "")

        print("\n7. Статус для поллинга")
        r = await c.get(f"/api/applications/{app_id}/status", params={"applicant_token": token})
        st = r.json()
        check("показаны все обязательные типы", len(st["required_documents"]) == 3)
        check("недостающие посчитаны", set(st["missing_documents"]) == {"snils", "inn"},
              str(st["missing_documents"]))
        check("статус заявки processing", st["status"] == "processing", st["status"])

        print("\n8. Страницы соискателя")
        r = await c.get("/")
        check("GET / отдаёт страницу выбора страны",
              r.status_code == 200 and "Узбекистан" in r.text, str(r.status_code))
        r = await c.get(f"/a/{token}")
        check("GET /a/{token} отдаёт карточку заявки",
              r.status_code == 200 and "Паспорт РФ" in r.text, str(r.status_code))
        r = await c.get(f"/a/{token}/status")
        check("htmx-фрагмент статусов отдаётся",
              r.status_code == 200 and 'id="status"' in r.text, str(r.status_code))
        check("поллинг включён, пока документ не разобран", "every 3s" in r.text)
        r = await c.get("/a/00000000-0000-0000-0000-000000000000")
        check("страница по чужому токену -> 404", r.status_code == 404, str(r.status_code))

    await engine.dispose()

    print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
