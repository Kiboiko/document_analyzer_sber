# CLAUDE.md — приём и разбор документов соискателей

Сервис кадрового агентства: соискатель грузит фото документов → система в фоне
распознаёт и извлекает поля → оператор проверяет распознанное рядом с оригиналом
и правит. Автономный, своё хранилище, своё рабочее место оператора.
Полное ТЗ: `TZ.md`. Условия кейса: `case.docx` (не редактировать).

## Стек
- Backend: Python 3.12, FastAPI, SQLAlchemy 2 (async) + asyncpg, Pydantic v2.
- БД: PostgreSQL 16. Таблицы создаются на старте (`Base.metadata.create_all`).
- Фон: отдельный процесс-воркер (`python -m app.worker`), очередь в Postgres
  (`SELECT ... FOR UPDATE SKIP LOCKED`), параллельность через asyncio-семафор.
- Фронт: FastAPI + Jinja2 + htmx (минимум JS, статусы без ручного обновления).
- Auth: Keycloak (OIDC, Authorization Code). Роли `admin`, `operator`.
  Соискатель — без входа, доступ к своей заявке по невычисляемому токену.
- Модель: OCR = HTTP-сервис организаторов по `OCR_SERVICE_URL` (за ним olmOCR); LLM = Qwen (внешний
  API ilons) или локальный (Ollama). Все вызовы — только в `app/model/`.
- Инфра: docker-compose (postgres, keycloak, app, worker; профиль ollama).

## Архитектура
```mermaid
flowchart LR
  A[Соискатель: публичная страница] -->|upload| API[FastAPI backend]
  OP[Оператор/Админ: кабинет OIDC] --> API
  KC[Keycloak OIDC] --- OP
  API --> DB[(PostgreSQL:\nзаявки, документы, файлы,\nполя, задачи, события)]
  API -->|enqueue| JOBS[(jobs в БД)]
  W[worker-процесс] -->|SKIP LOCKED| JOBS
  W --> M[app.model.recognize]
  M --> OCR[OCR-сервис\nOCR_SERVICE_URL]
  M --> LLM[Qwen: ilons API\nили Ollama local]
  W --> DB
  API -->|poll/htmx| A
```

## Карта репозитория
```
app/
  main.py            # сборка FastAPI, монтирование роутов, create_all на старте
  config.py          # Settings из env (pydantic-settings)
  db.py              # async engine, session, Base
  models.py          # ORM: Application, Document, File, Field, Job, Event, CrossCheck
  schemas.py         # Pydantic DTO
  enums.py           # Country, DocType, DocStatus, JobStatus, EventType, FieldSource
  routers/
    public.py        # соискатель: создать заявку, загрузка, статус (токен)
    operator.py      # кабинет: список, карточка, правка поля, повторный разбор, очередь
    admin.py         # админ: создать/список операторов (Keycloak Admin API)
    auth.py          # OIDC login/callback/logout
  services/
    intake.py        # приём файла: тип/размер/объём/дедуп по sha256, enqueue
    queue.py         # постановка/выборка задач, реапер зависших
    history.py       # запись и чтение событий заявки (таймлайн)
    crosscheck.py    # сопоставление ФИО и дат между документами заявки
    keycloak.py      # admin-token, создание пользователя + роль operator
  model/
    __init__.py      # recognize(doc_type, image_bytes) -> ExtractResult
    ocr.py           # клиент HTTP-сервиса OCR (файл как есть -> текст markdown)
    providers/cloud.py  # LLM через ilons Qwen
    providers/local.py  # LLM/VLM через Ollama
    prompts/<doc_type>.txt
    validate.py      # правила проверки полей по типу (раздел 4.1 ТЗ)
  worker.py          # цикл воркера
  templates/ , static/
docs/                # README(запуск), SCHEMA.md(схема+пояснения), DECISIONS.md,
                     # LOCAL_MODEL.md, LIMITATIONS.md, AI_REPORT.md
keycloak/realm-export.json   # realm, роли, стартовый админ
.env.example , docker-compose.yml , Dockerfile , .gitignore
```

## Контракт модельного слоя (единственная точка вызова модели)
```python
## LLM Qwen (ilons) — reasoning-модель
cloud.py: брать message.content (не reasoning_content); max_tokens 1500-2000;
проверять finish_reason != "length" и content не пустой, иначе retry/ошибка;
temperature=0; парсить первый JSON-объект из content.
# app/model/__init__.py
async def recognize(doc_type: DocType, image_bytes: bytes) -> ExtractResult: ...
# ExtractResult: {fields: {name: value}, raw_text: str, provider: str, usage: {...}}
```
Поля извлекаются: step1 OCR (olmOCR → текст), step2 LLM извлекает JSON по
промпту `prompts/<doc_type>.txt`. Провайдер LLM выбирается `MODEL_PROVIDER`.
Валидация — отдельно в `validate.py`, не внутри промпта.

## Переменные окружения (значения — только в .env, не в git)
```
DATABASE_URL=postgresql+asyncpg://app:app@postgres:5432/app
MODEL_PROVIDER=cloud            # cloud | local
OCR_SERVICE_URL=                # по умолчанию https://ocr.ilons.ru
OCR_CLIENT_TOKEN=               # X-API-Token; пусто = открытый доступ
OCR_TIMEOUT_S=                  # >= 300 (дедлайн сервиса 240 с)
LLM_BASE_URL=https://llm.ilons.ru
ILONS_API_KEY=                  # внешний ключ, в репо НЕ класть
LLM_MODEL=Qwen/Qwen3.8-27B      # сверить с /v1/models!
OLLAMA_HOST=http://ollama:11434
LOCAL_VLM_MODEL=qwen2.5vl:3b
OIDC_ISSUER=http://keycloak:8080/realms/docs
OIDC_CLIENT_ID=docs-app
OIDC_CLIENT_SECRET=
KEYCLOAK_ADMIN_URL=http://keycloak:8080
STORAGE_DIR=./data/files
MAX_UPLOAD_MB=20
MAX_FILES_PER_REQUEST=30


```

## Железные правила (иначе снятие с конкурса / потеря допуска)
- Любая проверка бизнес-результата — на сервере, не на клиенте.
- Всё в Postgres и переживает рестарт. Память процесса/файл рядом с кодом — нельзя.
- Приём мгновенный, разбор в фоне. Не блокировать соискателя ожиданием.
- Статус `готово` ставится только после реального вызова модели.
- Кабинет закрыт OIDC; по прямой ссылке без входа PII не отдаётся.
- Секретов в репо нет. В git — только `.env.example` без значений.
- Каждое значимое действие → запись в `events` (журнал/история).
- Ошибка модели/битый ввод → понятный статус, retry, без падений и без выдуманных данных.

## Маршрутизация ИИ (экономия токенов)
- 🔴 **Claude Code** — сложное: воркер/очередь (SKIP LOCKED, реапер, рестарт),
  OIDC-поток, Keycloak Admin API, абстракция провайдеров модели, промпты,
  правила валидации, сопоставление ФИО/дат.
- 🟢 **Copilot** — простое/шаблонное: Pydantic-схемы, CRUD-ручки, Jinja-шаблоны,
  Dockerfile, .env.example, мелкие правки, тесты-болванки.
- `/clear` между несвязанными задачами. Точечные промты с путём к файлу,
  не «осмотри проект». Правки через Edit, не переписывание файла целиком.


## Git
Commit messages: English, Conventional Commits (feat/fix/docs/chore), imperative.