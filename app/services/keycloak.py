"""Клиент Keycloak Admin API: операторы и роли (TZ 1).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры, тел нет.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.enums import Role
from app.schemas import OperatorCreated, OperatorOut


class KeycloakAdminError(RuntimeError):
    """Keycloak Admin API ответил ошибкой (сеть, 4xx/5xx)."""


async def admin_token() -> str:
    """Токен bootstrap-админа master-realm (password grant, admin-cli).

    Используем его, а не service account клиента `docs-app`: так Admin API
    работает сразу после `--import-realm`, без ручной выдачи client-ролей
    `realm-management` сервис-аккаунту.
    """
    url = f"{settings.keycloak_admin_url}/realms/master/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": "admin-cli",
        "username": settings.keycloak_admin_user,
        "password": settings.keycloak_admin_password,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, data=data)
    if resp.status_code != 200:
        raise KeycloakAdminError(f"admin token: {resp.status_code} {resp.text}")
    return resp.json()["access_token"]


def _realm_base() -> str:
    return f"{settings.keycloak_admin_url}/admin/realms/{settings.keycloak_realm}"


async def create_operator(username: str, email: str) -> OperatorCreated:
    """Создать пользователя, выдать роль operator и временный пароль."""
    token = await admin_token()
    headers = {"Authorization": f"Bearer {token}"}
    temp_password = secrets.token_urlsafe(9)

    async with httpx.AsyncClient(timeout=10) as client:
        create_resp = await client.post(
            f"{_realm_base()}/users",
            headers=headers,
            json={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": False,
                "credentials": [
                    {"type": "password", "value": temp_password, "temporary": True}
                ],
            },
        )
        if create_resp.status_code == 409:
            raise KeycloakAdminError(f"пользователь '{username}' уже существует")
        if create_resp.status_code != 201:
            raise KeycloakAdminError(
                f"create user: {create_resp.status_code} {create_resp.text}"
            )

        location = create_resp.headers.get("Location", "")
        user_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not user_id:
            found = await client.get(
                f"{_realm_base()}/users",
                headers=headers,
                params={"username": username, "exact": "true"},
            )
            found.raise_for_status()
            users = found.json()
            if not users:
                raise KeycloakAdminError("пользователь создан, но не найден")
            user_id = users[0]["id"]

        role_resp = await client.get(
            f"{_realm_base()}/roles/{Role.OPERATOR.value}", headers=headers
        )
        role_resp.raise_for_status()
        role = role_resp.json()

        assign_resp = await client.post(
            f"{_realm_base()}/users/{user_id}/role-mappings/realm",
            headers=headers,
            json=[{"id": role["id"], "name": role["name"]}],
        )
        if assign_resp.status_code not in (204,):
            raise KeycloakAdminError(
                f"assign role: {assign_resp.status_code} {assign_resp.text}"
            )

    return OperatorCreated(
        id=user_id,
        username=username,
        email=email,
        temporary_password=temp_password,
    )


async def list_operators() -> list[OperatorOut]:
    """Пользователи realm с ролью operator."""
    raise NotImplementedError  # TODO
