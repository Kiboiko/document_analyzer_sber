"""ORM-модели (TZ 4). Всё состояние живёт здесь и переживает рестарт."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
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

# Короткие алиасы для повторяющихся типов колонок.
_UUID = UUID(as_uuid=True)
_TS = DateTime(timezone=True)


def _uuid4() -> uuid.UUID:
    return uuid.uuid4()


class Application(Base):
    """Заявка соискателя. Доступ соискателя — по applicant_token."""

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    country: Mapped[Country] = mapped_column(String(8), nullable=False)
    # Невычисляемый токен для публичной ссылки (TZ 1).
    applicant_token: Mapped[uuid.UUID] = mapped_column(
        _UUID, nullable=False, unique=True, index=True, default=_uuid4
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        String(16), nullable=False, default=ApplicationStatus.CREATED
    )
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="Document.created_at",
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="application",
        cascade="all, delete-orphan",
        order_by="Event.created_at",
    )
    cross_checks: Mapped[list["CrossCheck"]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_applications_country_status", "country", "status"),)


class Document(Base):
    """Документ заявки одного из восьми типов."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_type: Mapped[DocType] = mapped_column(String(32), nullable=False)
    status: Mapped[DocStatus] = mapped_column(
        String(16), nullable=False, default=DocStatus.ACCEPTED
    )
    # Понятная человеку причина error / flagged.
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    application: Mapped[Application] = relationship(back_populates="documents")
    files: Mapped[list["File"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="File.created_at",
    )
    fields: Mapped[list["Field"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="Field.name",
    )
    jobs: Mapped[list["Job"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="Job.created_at",
    )

    __table_args__ = (Index("ix_documents_app_type", "application_id", "doc_type"),)


class File(Base):
    """Загруженный файл документа. Дедуп повторной загрузки — (document_id, sha256)."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Путь внутри STORAGE_DIR. Содержимое файлов в БД не храним.
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    orig_name: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="files")

    __table_args__ = (
        UniqueConstraint("document_id", "sha256", name="uq_files_document_sha256"),
    )


class Field(Base):
    """Извлечённое (или исправленное оператором) поле документа."""

    __tablename__ = "fields"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Каноническое имя из enums.EXTRACTED_FIELDS[doc_type].
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[FieldSource] = mapped_column(
        String(16), nullable=False, default=FieldSource.MODEL
    )
    # Уверенность модели, 0..1 (может отсутствовать).
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_valid: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Текст замечания проверки (TZ 3), если поле не прошло правило.
    validation_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="fields")

    __table_args__ = (
        UniqueConstraint("document_id", "name", name="uq_fields_document_name"),
    )


class Job(Base):
    """Задача фонового разбора. Очередь — в Postgres (FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        String(16), nullable=False, default=JobStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Когда воркер взял задачу. Устаревший lock — реапер вернёт в pending.
    locked_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Текст последней ошибки провайдера или разбора.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Фактические границы разбора — для счётчиков очереди (TZ 5).
    started_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    document: Mapped[Document] = relationship(back_populates="jobs")

    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)


class Event(Base):
    """Событие таймлайна заявки (TZ 9): кто, когда, что, причина."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        _UUID,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    type: Mapped[EventType] = mapped_column(String(32), nullable=False)
    # Кто инициировал: applicant, worker, логин оператора или админа.
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )

    application: Mapped[Application] = relationship(back_populates="events")

    __table_args__ = (Index("ix_events_app_created", "application_id", "created_at"),)


class CrossCheck(Base):
    """Результат сопоставления ФИО и дат между документами заявки (TZ 8)."""

    __tablename__ = "cross_checks"

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=_uuid4)
    application_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[CrossCheckKind] = mapped_column(String(16), nullable=False)
    status: Mapped[CrossCheckStatus] = mapped_column(String(16), nullable=False)
    # Человекочитаемое пояснение расхождения.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    application: Mapped[Application] = relationship(back_populates="cross_checks")

    __table_args__ = (
        UniqueConstraint("application_id", "kind", name="uq_cross_checks_app_kind"),
    )
