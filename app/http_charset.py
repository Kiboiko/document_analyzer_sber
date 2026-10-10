"""Явный charset=utf-8 у всех JSON-ответов.

Starlette ставит `Content-Type: application/json` без charset: для JSON,
по RFC 8259, это допустимо, кодировка всегда UTF-8. Но не все клиенты это
знают. Windows PowerShell 5.1 (`Invoke-RestMethod`) без charset считает ответ
ISO-8859-1, и кириллица превращается в мойбейк (`Ð˜Ð’Ð...`), хотя байты на
проводе верные.

Почему middleware, а не `default_response_class`: ответы на ошибки
(`HTTPException` с русским `detail`, ошибки валидации) формируются
обработчиками исключений мимо класса ответа по умолчанию и остались бы без
charset. Middleware видит каждый ответ.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_JSON = b"application/json"


class JsonCharsetMiddleware:
    """Дописывает `; charset=utf-8` к `application/json`, если charset не указан."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_charset(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                for index, (name, value) in enumerate(headers):
                    if name.lower() != b"content-type":
                        continue
                    lowered = value.lower()
                    # Только JSON и только без charset: HTML, файлы и ответы,
                    # где charset уже задан, не трогаем.
                    if lowered.startswith(_JSON) and b"charset" not in lowered:
                        headers[index] = (name, value.rstrip() + b"; charset=utf-8")
                    break
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_charset)
