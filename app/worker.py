"""Фоновый воркер: отдельный процесс, `python -m app.worker` (TZ 6).

Цикл: реапер на старте → забирать задачи SKIP LOCKED → `recognize` →
поля + валидация + события → done/error/flagged. Параллельность —
asyncio-семафор (WORKER_CONCURRENCY). Любая ошибка провайдера не роняет процесс.

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import asyncio

from app.models import Job


def worker_id() -> str:
    """Идентификатор процесса-воркера (пишется в Job.worker_id)."""
    raise NotImplementedError  # TODO(Кирилл)


async def process_job(job: Job) -> None:
    """Разобрать один документ в своей сессии БД.

    Шаги: parse_started → recognize → validate → Field/Event → статус документа →
    crosscheck по заявке. Исключение здесь = mark_failed, а не падение воркера.
    """
    raise NotImplementedError  # TODO(Кирилл)


async def reaper_loop(stop: asyncio.Event) -> None:
    """Периодически возвращать зависшие processing в pending."""
    raise NotImplementedError  # TODO(Кирилл)


async def worker_loop(stop: asyncio.Event) -> None:
    """Опрашивать очередь и запускать разбор под семафором."""
    raise NotImplementedError  # TODO(Кирилл)


async def main() -> None:
    """Точка входа: реапер на старте, затем циклы до сигнала остановки."""
    raise NotImplementedError  # TODO(Кирилл)


if __name__ == "__main__":
    asyncio.run(main())
