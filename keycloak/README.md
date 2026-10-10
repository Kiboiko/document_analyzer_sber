# keycloak/

`realm-export.json` импортируется автоматически (`start-dev --import-realm`,
каталог смонтирован в `/opt/keycloak/data/import`).

Сейчас здесь минимальный каркас: realm `docs`, роли `admin` и `operator`,
клиент `docs-app` (confidential, Authorization Code, service account для
Admin API).

TODO(Кирилл):
- стартовый пользователь-админ в `users`;
- маппер ролей в токен, чтобы `CurrentUser.roles` заполнялся;
- права service account на управление пользователями realm (`realm-management`);
- секрет клиента НЕ коммитить: он живёт в `.env` (`OIDC_CLIENT_SECRET`).
