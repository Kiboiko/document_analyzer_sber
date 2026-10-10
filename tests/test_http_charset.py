"""JSON-ответы идут с charset=utf-8, и клиент PowerShell 5.1 читает кириллицу верно.

Windows PowerShell 5.1 (`Invoke-RestMethod`) при отсутствии charset в
Content-Type считает ответ ISO-8859-1. Тест воспроизводит этот клиент:
`decode_like_ps51` берёт кодировку из заголовка и, если её нет, Latin-1.
БД не нужна.
"""

import asyncio
import json
import os
import re
import sys

os.environ.setdefault("SESSION_SECRET", "test-secret")

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.http_charset import JsonCharsetMiddleware
from app.main import app as real_app

OK, FAIL = [], []
TEXT = "ИВАНОВ ИВАН ИВАНОВИЧ"


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


def decode_like_ps51(resp: httpx.Response) -> str:
    """Как Invoke-RestMethod в PowerShell 5.1: charset из заголовка, иначе Latin-1."""
    match = re.search(r"charset=([\w-]+)", resp.headers.get("content-type", ""), re.I)
    return resp.content.decode(match.group(1) if match else "iso-8859-1")


def tiny_app(with_middleware: bool) -> FastAPI:
    app = FastAPI()
    if with_middleware:
        app.add_middleware(JsonCharsetMiddleware)

    @app.get("/data")
    async def data():
        return {"full_name": TEXT, "birth_place": "г. Москва"}

    @app.get("/missing")
    async def missing():
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    @app.get("/html", response_class=HTMLResponse)
    async def html():
        return "<p>Привет</p>"

    @app.get("/text", response_class=PlainTextResponse)
    async def text():
        return "Привет"

    @app.get("/already")
    async def already():
        return JSONResponse({"x": TEXT}, media_type="application/json; charset=utf-8")

    @app.get("/other-charset")
    async def other():
        return JSONResponse({"x": "x"}, media_type="application/json; charset=windows-1251")

    @app.get("/empty", status_code=204)
    async def empty():
        return None

    return app


async def get(app, path):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path)


async def main():
    print("\n1. Без middleware клиент PowerShell 5.1 портит кириллицу (воспроизведение)")
    r = await get(tiny_app(False), "/data")
    check("заголовок без charset", r.headers["content-type"] == "application/json",
          r.headers["content-type"])
    check("клиент, читающий как Latin-1, получает мойбейк",
          json.loads(decode_like_ps51(r))["full_name"] != TEXT)
    check("при этом байты на проводе верные (UTF-8)",
          json.loads(r.content.decode("utf-8"))["full_name"] == TEXT)

    print("\n2. С middleware тот же клиент читает верно")
    r = await get(tiny_app(True), "/data")
    check("Content-Type: application/json; charset=utf-8",
          r.headers["content-type"] == "application/json; charset=utf-8",
          r.headers["content-type"])
    check("клиент, читающий по заголовку, получает кириллицу",
          json.loads(decode_like_ps51(r)) == {"full_name": TEXT, "birth_place": "г. Москва"})
    check("тело ответа не изменено",
          r.content == json.dumps({"full_name": TEXT, "birth_place": "г. Москва"},
                                  ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    print("\n3. Ответы об ошибках с русским текстом тоже")
    r = await get(tiny_app(True), "/missing")
    check("HTTPException 404: charset есть",
          r.status_code == 404 and r.headers["content-type"] == "application/json; charset=utf-8",
          f"{r.status_code} {r.headers['content-type']}")
    check("русский detail читается верно", json.loads(decode_like_ps51(r))["detail"] == "Заявка не найдена")

    print("\n4. Не-JSON и уже размеченные ответы не трогаем")
    app = tiny_app(True)
    r = await get(app, "/html")
    check("HTML без изменений и без дубля charset",
          r.headers["content-type"] == "text/html; charset=utf-8", r.headers["content-type"])
    r = await get(app, "/text")
    check("text/plain без изменений", r.headers["content-type"] == "text/plain; charset=utf-8",
          r.headers["content-type"])
    r = await get(app, "/already")
    check("charset уже есть: не дублируется",
          r.headers["content-type"] == "application/json; charset=utf-8", r.headers["content-type"])
    r = await get(app, "/other-charset")
    check("чужой явный charset не перезаписывается",
          r.headers["content-type"] == "application/json; charset=windows-1251",
          r.headers["content-type"])
    r = await get(app, "/empty")
    check("ответ без тела (204) не ломается", r.status_code == 204, str(r.status_code))

    print("\n5. Не-HTTP события проходят насквозь")
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await JsonCharsetMiddleware(inner)({"type": "lifespan"}, None, None)
    check("lifespan передан дальше без изменений", seen == ["lifespan"], str(seen))

    print("\n6. Настоящее приложение")
    r = await get(real_app, "/health")
    check("GET /health: charset=utf-8",
          r.headers["content-type"] == "application/json; charset=utf-8", r.headers["content-type"])
    r = await get(real_app, "/api/applications/не-uuid/status?applicant_token=x")
    check("ошибка валидации (422) тоже с charset",
          r.status_code == 422 and r.headers["content-type"] == "application/json; charset=utf-8",
          f"{r.status_code} {r.headers['content-type']}")
    r = await get(real_app, "/")
    check("HTML-страница не затронута", r.headers["content-type"] == "text/html; charset=utf-8",
          r.headers["content-type"])

    print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
    if FAIL:
        print("ПРОВАЛЫ:", FAIL)
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
