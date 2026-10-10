"""Предохранитель для тестов, которые пересоздают схему БД.

Тесты приёма и сопоставления делают `DROP SCHEMA public CASCADE`. Настройки
читают `.env`, поэтому без явной переменной в окружении тест тихо взял бы
рабочую базу разработчика. Требуем, чтобы адрес был задан в окружении процесса
(в `os.environ`, а не в `.env`) и указывал на локальный хост.
"""

import os
import sys
from urllib.parse import urlparse

_LOCAL = {"127.0.0.1", "localhost", "::1"}


def require_throwaway_db() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit(
            "Отказ: DATABASE_URL не задан в окружении. Тест пересоздаёт схему БД "
            "и не должен брать адрес из .env. См. tests/README.md."
        )
    host = urlparse(url.replace("+asyncpg", "")).hostname
    if host not in _LOCAL:
        sys.exit(
            f"Отказ: хост БД {host!r} не локальный. Тест удаляет все таблицы, "
            "запускать его можно только на одноразовой локальной базе."
        )
