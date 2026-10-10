"""Админ: создание операторов через Keycloak Admin API (TZ 1, 5).

Владелец: Кирилл (ветка feat/infra-operator).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.enums import Role
from app.routers.auth import current_user
from app.schemas import CurrentUser, OperatorCreate, OperatorCreated, OperatorOut
from app.services import keycloak

router = APIRouter(tags=["admin"])


async def require_admin(request: Request) -> CurrentUser:
    """Зависимость: пускать только с ролью admin. Проверка — на сервере."""
    user = current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "требуется вход")
    if not user.has_role(Role.ADMIN.value):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "требуется роль admin")
    return user


@router.get("/admin/operators", response_class=HTMLResponse)
async def page_operators(
    request: Request,
    user: CurrentUser = Depends(require_admin),
) -> HTMLResponse:
    """Страница: список операторов + форма создания."""
    operators = await keycloak.list_operators()
    return request.app.state.templates.TemplateResponse(
        request,
        "admin/operators.html",
        {"user": user, "operators": operators},
    )


@router.get("/api/admin/operators", response_model=list[OperatorOut])
async def list_operators(
    user: CurrentUser = Depends(require_admin),
) -> list[OperatorOut]:
    """Список операторов из Keycloak."""
    return await keycloak.list_operators()


@router.post("/api/admin/operators", response_model=OperatorCreated)
async def create_operator(
    payload: OperatorCreate,
    user: CurrentUser = Depends(require_admin),
) -> OperatorCreated:
    """Создать пользователя в Keycloak + роль operator + временный пароль."""
    try:
        return await keycloak.create_operator(payload.username, payload.email)
    except keycloak.KeycloakAdminError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
