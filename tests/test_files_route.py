"""Выдача оригинала файла оператору: цепочка заявка -> документ -> файл.

Требует одноразовую БД (схема пересоздаётся).
"""

import asyncio
import io
import sys
import tempfile
import uuid
from pathlib import Path

from _safety import require_throwaway_db

require_throwaway_db()

import os

# Хранилище — во временном каталоге, чтобы проверять выход за его пределы.
_TMP = tempfile.mkdtemp(prefix="da_files_")
os.environ["STORAGE_DIR"] = str(Path(_TMP) / "storage")

import httpx
from PIL import Image
from sqlalchemy import select, text

from app.db import create_all, engine, session_factory
from app.main import app
from app.models import File
from app.routers.operator import require_operator
from app.schemas import CurrentUser

OK, FAIL = [], []
OPERATOR = CurrentUser(sub="op-1", username="operator1", roles=["operator"])


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


def jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, format="JPEG")
    return buf.getvalue()


async def make_application(client, doc_type, data):
    """Заявка с одним загруженным файлом через настоящий публичный API."""
    r = await client.post("/api/applications", json={"country": "ru"})
    created = r.json()
    r = await client.post(
        f"/api/applications/{created['id']}/documents",
        params={"applicant_token": created["applicant_token"]},
        data={"doc_type": doc_type},
        files=[("files", ("scan.jpg", data, "image/jpeg"))],
    )
    up = r.json()
    return created["id"], up["document_id"], up["accepted"][0]["id"]


def url(app_id, doc_id, file_id):
    return f"/api/operator/applications/{app_id}/documents/{doc_id}/files/{file_id}"


async def main():
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await create_all()

    bytes_a, bytes_b = jpeg("red"), jpeg("blue")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        app_a, doc_a, file_a = await make_application(c, "passport_rf", bytes_a)
        app_b, doc_b, file_b = await make_application(c, "passport_rf", bytes_b)

        print("\n1. Без входа оригинал не отдаётся")
        # require_operator пока заглушка (зона Кирилла) и падает: роут закрыт.
        # Когда он реализует вход, здесь будет 401/403/редирект — проверка
        # написана так, чтобы оставаться верной и тогда.
        r = await c.get(url(app_a, doc_a, file_a))
        check("без сессии оператора не 200", r.status_code != 200, str(r.status_code))
        check("тело ответа не содержит байтов файла", bytes_a[:64] not in r.content)

        app.dependency_overrides[require_operator] = lambda: OPERATOR

        print("\n2. Оператор получает свой файл")
        r = await c.get(url(app_a, doc_a, file_a))
        check("200", r.status_code == 200, str(r.status_code))
        check("байты совпадают с загруженными", r.content == bytes_a)
        check("Content-Type из File.mime", r.headers["content-type"] == "image/jpeg",
              r.headers["content-type"])
        check("inline и имя файла",
              r.headers["content-disposition"].startswith("inline")
              and "scan.jpg" in r.headers["content-disposition"],
              r.headers["content-disposition"])
        check("не кэшируется", "no-store" in r.headers["cache-control"])
        check("nosniff", r.headers["x-content-type-options"] == "nosniff")

        r = await c.get(url(app_b, doc_b, file_b))
        check("файл второй заявки отдаётся по своей цепочке", r.content == bytes_b)

        print("\n3. Подмена любого из трёх id -> 404")
        cases = {
            "чужая заявка, свой документ и файл": url(app_b, doc_a, file_a),
            "чужой документ, своя заявка": url(app_a, doc_b, file_a),
            "чужой файл в своём документе": url(app_a, doc_a, file_b),
            "всё от второй заявки, но заявка первая": url(app_a, doc_b, file_b),
            "несуществующий файл": url(app_a, doc_a, uuid.uuid4()),
            "несуществующая заявка": url(uuid.uuid4(), doc_a, file_a),
        }
        for title, u in cases.items():
            r = await c.get(u)
            check(f"{title} -> 404", r.status_code == 404, str(r.status_code))
            check(f"{title}: байтов ни одного из файлов в ответе нет",
                  bytes_a[:64] not in r.content and bytes_b[:64] not in r.content)

        r1 = await c.get(url(app_b, doc_a, file_a))
        r2 = await c.get(url(app_a, doc_a, uuid.uuid4()))
        check("причины неразличимы по ответу", r1.content == r2.content,
              f"{r1.text} / {r2.text}")

        print("\n4. stored_path вне хранилища не выдаётся")
        outside = Path(_TMP) / "secret.txt"
        outside.write_bytes(b"TOP SECRET OUTSIDE STORAGE")
        for title, bad in [("../ выход наверх", "../secret.txt"),
                           ("абсолютный путь", str(outside)),
                           ("глубокий ../", "a/b/../../../secret.txt")]:
            async with session_factory() as s:
                rec = await s.scalar(select(File).where(File.id == uuid.UUID(file_a)))
                good = rec.stored_path
                rec.stored_path = bad
                await s.commit()
            r = await c.get(url(app_a, doc_a, file_a))
            check(f"{title} -> 404", r.status_code == 404, str(r.status_code))
            check(f"{title}: содержимое снаружи не утекло",
                  b"TOP SECRET" not in r.content)
            async with session_factory() as s:
                rec = await s.scalar(select(File).where(File.id == uuid.UUID(file_a)))
                rec.stored_path = good
                await s.commit()

        r = await c.get(url(app_a, doc_a, file_a))
        check("после восстановления пути файл снова отдаётся", r.content == bytes_a)

        print("\n5. Файл есть в БД, но пропал с диска")
        async with session_factory() as s:
            rec = await s.scalar(select(File).where(File.id == uuid.UUID(file_a)))
            disk = Path(os.environ["STORAGE_DIR"]) / rec.stored_path
        disk.rename(disk.with_suffix(".moved"))
        r = await c.get(url(app_a, doc_a, file_a))
        check("-> 404, а не 500", r.status_code == 404, str(r.status_code))
        disk.with_suffix(".moved").rename(disk)

        print("\n6. Имя файла от клиента не ломает заголовки")
        for title, name in [("кавычки и CRLF", 'a"b\r\nX-Evil: 1.jpg'),
                            ("слеши", "..\\..\\evil/name.jpg"),
                            ("кириллица", "паспорт скан.jpg"),
                            ("пустое после очистки", "\r\n")]:
            async with session_factory() as s:
                rec = await s.scalar(select(File).where(File.id == uuid.UUID(file_a)))
                rec.orig_name = name
                await s.commit()
            r = await c.get(url(app_a, doc_a, file_a))
            disp = r.headers.get("content-disposition", "")
            check(f"{title}: 200", r.status_code == 200, str(r.status_code))
            check(f"{title}: нет подмешанных заголовков",
                  "x-evil" not in {k.lower() for k in r.headers}, disp)
            check(f"{title}: заголовок без CR/LF", "\r" not in disp and "\n" not in disp)
        if True:
            r = await c.get(url(app_a, doc_a, file_a))
            check("пустое имя заменено на document", "document" in r.headers["content-disposition"],
                  r.headers["content-disposition"])
            async with session_factory() as s:
                rec = await s.scalar(select(File).where(File.id == uuid.UUID(file_a)))
                rec.orig_name = "паспорт скан.jpg"
                await s.commit()
            r = await c.get(url(app_a, doc_a, file_a))
            check("кириллица передана через filename*=UTF-8",
                  "filename*=utf-8''" in r.headers["content-disposition"].lower(),
                  r.headers["content-disposition"])

        print("\n7. Публичные ссылки соискателя этим роутом не открываются")
        app.dependency_overrides.clear()
        created_token = (await c.post("/api/applications", json={"country": "ru"})).json()
        r = await c.get(url(app_a, doc_a, file_a),
                        params={"applicant_token": created_token["applicant_token"]})
        check("токен соискателя доступа не даёт", r.status_code != 200, str(r.status_code))

    await engine.dispose()
    print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
