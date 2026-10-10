"""Админ: создание операторов через Keycloak Admin API (TZ 1, 5).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.schemas import CurrentUser, OperatorCreate, OperatorCreated, OperatorOut

router = APIRouter(tags=["admin"])


async def require_admin(request: Request) -> CurrentUser:
    """Зависимость: пускать только с ролью admin. Проверка — на сервере.

    TODO(Кирилл): читать OIDC-сессию, 403 при отсутствии роли.
    """
    raise NotImplementedError


@router.get("/admin/operators", response_class=HTMLResponse)
async def page_operators(
    request: Request,
    user: CurrentUser = Depends(require_admin),
) -> HTMLResponse:
    """Страница: список операторов + форма создания."""
    raise NotImplementedError  # TODO(Кирилл)


@router.get("/api/admin/operators", response_model=list[OperatorOut])
async def list_operators(
    user: CurrentUser = Depends(require_admin),
) -> list[OperatorOut]:
    """Список операторов из Keycloak."""
    raise NotImplementedError  # TODO(Кирилл): services.keycloak.list_operators


@router.post("/api/admin/operators", response_model=OperatorCreated)
async def create_operator(
    payload: OperatorCreate,
    user: CurrentUser = Depends(require_admin),
) -> OperatorCreated:
    """Создать пользователя в Keycloak + роль operator + временный пароль."""
    raise NotImplementedError  # TODO(Кирилл): services.keycloak.create_operator
