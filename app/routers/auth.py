"""OIDC Authorization Code через Keycloak (TZ 1, 5).

Владелец: Кирилл (ветка feat/infra-operator) — спайк №1 по рискам (TZ 14).
Каркас: только сигнатуры и контракты, тел нет.
"""

from __future__ import annotations

import base64
import json
from urllib.parse import urlencode, urljoin

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.enums import Role
from app.schemas import CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

oauth = OAuth()
oauth.register(
    name="keycloak",
    server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
    client_id=settings.oidc_client_id,
    client_secret=settings.oidc_client_secret,
    client_kwargs={"scope": "openid profile email"},
)

_KNOWN_ROLES = {Role.ADMIN.value, Role.OPERATOR.value}


def _jwt_claims(token: str) -> dict:
    """Payload JWT без проверки подписи.

    Токен получен напрямую от Keycloak внутри server-to-server обмена
    (token endpoint по HTTPS), а не от клиента — повторная сверка подписи
    здесь избыточна, нам нужны только claims (roles, sub, email).
    """
    segment = token.split(".")[1]
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


@router.get("/login")
async def login(request: Request, next: str = "/operator") -> RedirectResponse:
    """Редирект на Keycloak (authorization endpoint) + state/nonce в сессии."""
    request.session["next"] = next
    return await oauth.keycloak.authorize_redirect(request, settings.oidc_redirect_url)


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Обмен code на токены, проверка state/nonce и подписи, запись сессии."""
    if error or not code:
        return RedirectResponse(url="/auth/login")

    token = await oauth.keycloak.authorize_access_token(request)
    claims = _jwt_claims(token["access_token"])
    roles = claims.get("realm_access", {}).get("roles", [])

    user = CurrentUser(
        sub=claims["sub"],
        username=claims.get("preferred_username", claims["sub"]),
        email=claims.get("email"),
        roles=[r for r in roles if r in _KNOWN_ROLES],
    )
    request.session["user"] = user.model_dump()
    request.session["id_token"] = token.get("id_token")

    next_url = request.session.pop("next", "/operator")
    return RedirectResponse(url=next_url)


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """Сброс серверной сессии + end_session на стороне Keycloak."""
    id_token = request.session.pop("id_token", None)
    request.session.pop("user", None)

    metadata = await oauth.keycloak.load_server_metadata()
    end_session_endpoint = metadata.get("end_session_endpoint")
    if not end_session_endpoint:
        return RedirectResponse(url="/")

    post_logout_redirect_uri = urljoin(settings.oidc_redirect_url, "/")
    params = {"post_logout_redirect_uri": post_logout_redirect_uri}
    if id_token:
        params["id_token_hint"] = id_token
    return RedirectResponse(url=f"{end_session_endpoint}?{urlencode(params)}")


def current_user(request: Request) -> CurrentUser | None:
    """Прочитать пользователя из сессии; None — если не вошёл.

    Единственное место чтения OIDC-сессии: роутеры ходят через зависимости
    `require_operator` / `require_admin`.
    """
    data = request.session.get("user")
    if not data:
        return None
    return CurrentUser(**data)
