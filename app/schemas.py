"""Pydantic-DTO (TZ 5). Точка стыковки двух веток: меняем только договорившись."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field as PydField

from app.enums import (
    ApplicationStatus,
    Country,
    CrossCheckKind,
    CrossCheckStatus,
    DocStatus,
    DocType,
    EventType,
    FieldSource,
    JobStatus,
)


class ORMModel(BaseModel):
    """База для DTO, собираемых из ORM-объектов."""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Поля, файлы, документы
# --------------------------------------------------------------------------- #


class FieldOut(ORMModel):
    """Поле документа в выдаче."""

    id: uuid.UUID
    name: str
    title: str | None = None  # подпись для UI (enums.FIELD_TITLES)
    value: str | None = None
    source: FieldSource
    confidence: float | None = None
    is_valid: bool
    validation_msg: str | None = None
    updated_at: datetime


class FieldPatch(BaseModel):
    """Правка поля оператором: PATCH .../fields/{field_id}."""

    value: str | None = PydField(default=None, max_length=1024)


class FileOut(ORMModel):
    """Загруженный файл. Путь в хранилище наружу не отдаём."""

    id: uuid.UUID
    orig_name: str
    mime: str
    size: int
    created_at: datetime


class DocumentOut(ORMModel):
    """Документ со статусом — основа поллинга соискателя."""

    id: uuid.UUID
    doc_type: DocType
    title: str | None = None  # enums.DOC_TYPE_TITLES
    status: DocStatus
    error_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    files: list[FileOut] = []
    fields: list[FieldOut] = []


# --------------------------------------------------------------------------- #
# События и сопоставление
# --------------------------------------------------------------------------- #


class EventOut(ORMModel):
    """Элемент таймлайна заявки (TZ 9)."""

    id: uuid.UUID
    document_id: uuid.UUID | None = None
    type: EventType
    actor: str
    payload: dict[str, Any] = {}
    created_at: datetime


class CrossCheckOut(ORMModel):
    """Замечание сопоставления ФИО/дат (TZ 8)."""

    id: uuid.UUID
    kind: CrossCheckKind
    status: CrossCheckStatus
    detail: str | None = None
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Публичная часть (соискатель)
# --------------------------------------------------------------------------- #


class ApplicationCreate(BaseModel):
    """POST /api/applications."""

    country: Country


class ApplicationCreated(BaseModel):
    """Ответ на создание заявки: ссылка соискателя строится из токена."""

    id: uuid.UUID
    applicant_token: uuid.UUID
    country: Country
    required_documents: list[DocType] = []


class UploadRejected(BaseModel):
    """Отклонённый файл — с понятной причиной (тип, размер, объём, дубль)."""

    orig_name: str
    reason: str


class UploadResult(BaseModel):
    """Ответ на POST /api/applications/{id}/documents.

    Приём мгновенный: разбор уже поставлен в очередь, ждать его не нужно.
    """

    document_id: uuid.UUID
    doc_type: DocType
    status: DocStatus
    accepted: list[FileOut] = []
    duplicates: list[FileOut] = []
    rejected: list[UploadRejected] = []


class ApplicationStatusOut(ORMModel):
    """GET /api/applications/{id}/status — поллинг htmx."""

    id: uuid.UUID
    country: Country
    status: ApplicationStatus
    created_at: datetime
    required_documents: list[DocType] = []
    #: Типы из обязательного набора, по которым файлов ещё нет.
    missing_documents: list[DocType] = []
    documents: list[DocumentOut] = []


# --------------------------------------------------------------------------- #
# Кабинет оператора / админа
# --------------------------------------------------------------------------- #


class ApplicationListItem(ORMModel):
    """Строка списка заявок в кабинете."""

    id: uuid.UUID
    country: Country
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime
    documents_total: int = 0
    documents_done: int = 0
    documents_error: int = 0
    documents_flagged: int = 0
    has_mismatch: bool = False


class ApplicationListOut(BaseModel):
    """GET /api/operator/applications?country&status&q."""

    items: list[ApplicationListItem] = []
    total: int = 0
    limit: int = 50
    offset: int = 0


class ApplicationCard(ORMModel):
    """GET /api/operator/applications/{id} — «оригинал рядом с полями»."""

    id: uuid.UUID
    country: Country
    status: ApplicationStatus
    created_at: datetime
    updated_at: datetime
    required_documents: list[DocType] = []
    missing_documents: list[DocType] = []
    documents: list[DocumentOut] = []
    events: list[EventOut] = []
    cross_checks: list[CrossCheckOut] = []


class QueueStats(BaseModel):
    """GET /api/operator/queue — очередь в цифрах (TZ 5)."""

    pending: int = 0
    processing: int = 0
    failed: int = 0
    done_last_hour: int = 0
    #: Среднее время разбора одной задачи, секунды.
    avg_duration_s: float | None = None
    oldest_pending_age_s: float | None = None


class JobOut(ORMModel):
    """Задача очереди — для раздела «в работе»."""

    id: uuid.UUID
    document_id: uuid.UUID
    status: JobStatus
    attempts: int
    worker_id: str | None = None
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ReparseResult(BaseModel):
    """POST /api/operator/documents/{id}/reparse."""

    document_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus


# --------------------------------------------------------------------------- #
# Админ
# --------------------------------------------------------------------------- #


class OperatorCreate(BaseModel):
    """POST /api/admin/operators."""

    username: str = PydField(min_length=3, max_length=64)
    email: str = PydField(max_length=254)


class OperatorCreated(BaseModel):
    """Созданный в Keycloak оператор + временный пароль (показать один раз)."""

    id: str
    username: str
    email: str
    temporary_password: str


class OperatorOut(BaseModel):
    """GET /api/admin/operators."""

    id: str
    username: str
    email: str | None = None
    enabled: bool = True
    created_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Текущий пользователь (OIDC)
# --------------------------------------------------------------------------- #


class CurrentUser(BaseModel):
    """Данные из OIDC-сессии кабинета."""

    sub: str
    username: str
    email: str | None = None
    roles: list[str] = []

    def has_role(self, role: str) -> bool:
        return role in self.roles


# --------------------------------------------------------------------------- #
# Контракт модельного слоя
# --------------------------------------------------------------------------- #


class ExtractResult(BaseModel):
    """Результат `app.model.recognize` — единственный выход модельного слоя.

    Валидацию полей здесь не делаем: это `app/model/validate.py`.
    """

    fields: dict[str, str | None] = {}
    raw_text: str = ""
    provider: str = ""
    usage: dict[str, Any] = {}
    #: Уверенность по полям, если провайдер её вернул (бонус TZ 12).
    confidence: dict[str, float] = {}


class FieldValidation(BaseModel):
    """Результат проверки одного поля (TZ 3)."""

    name: str
    is_valid: bool
    message: str | None = None


class ValidationReport(BaseModel):
    """Итог проверки документа: статус и замечания."""

    status: DocStatus
    fields: list[FieldValidation] = []
    #: Отсутствующие поля из минимального набора.
    missing_required: list[str] = []
    #: Замечания уровня документа (например, просрочка).
    notes: list[str] = []


class HealthOut(BaseModel):
    """GET /health."""

    status: str = "ok"
    app: str
    model_provider: str
