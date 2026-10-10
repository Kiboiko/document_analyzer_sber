"""OIDC Authorization Code через Keycloak (TZ 1, 5).

Владелец: Кирилл (ветка feat/infra-operator) — спайк №1 по рискам (TZ 14).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.schemas import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request, next: str = "/operator") -> RedirectResponse:
    """Редирект на Keycloak (authorization endpoint) + state/nonce в сессии."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get("/callback")
async def callback(request: Request, code: str, state: str) -> RedirectResponse:
    """Обмен code на токены, проверка state/nonce и подписи, запись сессии."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Сброс серверной сессии + end_session на стороне Keycloak."""
    raise NotImplementedError  # TODO(Кирилл)


def current_user(request: Request) -> CurrentUser | None:
    """Прочитать пользователя из сессии; None — если не вошёл.

    Единственное место чтения OIDC-сессии: роутеры ходят через зависимости
    `require_operator` / `require_admin`.
    """
    raise NotImplementedError  # TODO(Кирилл)
