"""Клиент Keycloak Admin API: операторы и роли (TZ 1).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

from app.schemas import OperatorCreated, OperatorOut


async def admin_token() -> str:
    """Service-account токен для Admin API (client_credentials)."""
    raise NotImplementedError  # TODO


async def create_operator(username: str, email: str) -> OperatorCreated:
    """Создать пользователя, выдать роль operator и временный пароль."""
    raise NotImplementedError  # TODO


async def list_operators() -> list[OperatorOut]:
    """Пользователи realm с ролью operator."""
    raise NotImplementedError  # TODO
