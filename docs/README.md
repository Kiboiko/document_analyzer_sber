# Запуск с нуля

TODO(заполнять по ходу, не в конце — Д10).

## 1. Окружение
1. `cp .env.example .env` и заполнить значения (ключи — не в git).
2. OCR: по умолчанию `OCR_SERVICE_URL=https://ocr.ilons.ru`. `OCR_CLIENT_TOKEN`
   нужен, только если сервис закрыт. Проверка: `curl https://ocr.ilons.ru/health`.
3. `MODEL_PROVIDER=cloud` для внешнего Qwen, `local` — для Ollama
   (см. `LOCAL_MODEL.md`).

## 2. Поднять стенд
```
docker compose up -d --build            # postgres, keycloak, app, worker
docker compose --profile ollama up -d   # дополнительно локальная модель
```

## 3. Проверить
- `GET http://localhost:8000/health` → `{"status":"ok", ...}`
- публичная страница соискателя: `http://localhost:8000/`
- кабинет оператора: `http://localhost:8000/operator` (редирект на Keycloak)

## 4. Учётки
- Админ кабинета: `admin1` / `admin1` (realm `docs`, временный пароль —
  Keycloak попросит сменить при первом входе). Задан в `keycloak/realm-export.json`.
- Операторов создаёт админ через `/admin/operators` (кнопка «создать») —
  это вызов Keycloak Admin API (`services/keycloak.py`), а не ручной ввод
  в Keycloak. Временный пароль показывается один раз в ответе.
- Bootstrap-админ master-realm Keycloak (для Admin API) — `KEYCLOAK_ADMIN_USER`
  / `KEYCLOAK_ADMIN_PASSWORD` из `.env` (не realm `docs`, а `master`).

### Браузер vs контейнер (важно для локального запуска)
`OIDC_ISSUER` — внутренний адрес Keycloak для серверных вызовов приложения
(`http://keycloak:8080/realms/docs` внутри docker-сети). Браузер оператора
этот хост не резолвит, поэтому если `app` не в той же сети, что браузер
(например, порт Keycloak просто опубликован на хост), нужно явно задать
`OIDC_PUBLIC_ISSUER=http://localhost:8080/realms/docs` — иначе редирект на
логин уйдёт на нерезолвящийся адрес. Подробнее — `DECISIONS.md`.

## 5. Что должно получиться
Сквозной сценарий (часть уже работает, часть ждёт `app.model.recognize`):
1. Соискатель на `/` выбирает страну → грузит документы → видит статусы
   (поллинг, без ручного обновления).
2. Приём мгновенный: файл сохранён, `Job(pending)` поставлен, ответ отдан
   сразу — разбор идёт в фоне (`python -m app.worker`).
3. Воркер берёт задачу (`SKIP LOCKED`), зовёт модель, пишет поля и статус
   документа, сравнивает ФИО/даты по заявке (`crosscheck`).
4. Оператор логинится через Keycloak (`/auth/login` → Keycloak → `/operator`),
   видит список заявок с фильтрами, открывает карточку «оригинал рядом с
   полями», правит поле или запускает повторный разбор.
5. Админ создаёт операторов на `/admin/operators` без прямого доступа к
   консоли Keycloak.
