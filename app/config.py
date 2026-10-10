"""Настройки приложения. Значения — только из окружения, в коде констант нет."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация сервиса (имена переменных — см. `.env.example`)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # `.env.example` содержит только имена переменных, без значений
        # (требование кейса). После `cp .env.example .env` там `DEBUG=`,
        # `MAX_UPLOAD_MB=` и т.п.; пустую строку pydantic не примет ни как
        # число, ни как bool. Пустое значение = «не задано», берём умолчание.
        env_ignore_empty=True,
    )

    # --- общее ------------------------------------------------------------- #
    app_name: str = "document-analyzer"
    debug: bool = False

    # --- БД ---------------------------------------------------------------- #
    database_url: str = "postgresql+asyncpg://app:app@postgres:5432/app"
    db_echo: bool = False

    # --- модель ------------------------------------------------------------ #
    #: cloud | local — смена провайдера без правки кода (TZ 7).
    model_provider: str = "cloud"

    # OCR: HTTP-сервис организаторов (olmOCR за ним). Принимает PDF/PNG/JPEG/HEIC
    # как есть и отдаёт текст markdown; конвертацию делает сам сервис.
    ocr_service_url: str = "https://ocr.ilons.ru"
    #: Токен сервиса (заголовок X-API-Token). Пусто — открытый доступ.
    ocr_client_token: str = ""
    #: Дедлайн самого сервиса 240 с; наш таймаут больше него с запасом на сеть,
    #: иначе мы оборвём запрос раньше, чем придёт осмысленный 504.
    ocr_timeout_s: int = 300

    # LLM cloud: Qwen через внешний API ilons
    llm_base_url: str = "https://llm.ilons.ru"
    ilons_api_key: str = ""
    llm_model: str = "Qwen/Qwen3.8-27B"
    #: Qwen у ilons — reasoning-модель: часть бюджета уходит на рассуждения,
    #: и на тесном лимите `content` приходит пустым. Меньше 1500 не ставить.
    llm_max_tokens: int = 2000

    # LLM/VLM local: Ollama
    ollama_host: str = "http://ollama:11434"
    local_vlm_model: str = "qwen2.5vl:3b"

    #: Таймаут запроса к модели, секунды.
    model_timeout_s: int = 180

    # --- OIDC / Keycloak --------------------------------------------------- #
    oidc_issuer: str = "http://keycloak:8080/realms/docs"
    #: Публичный адрес Keycloak для браузера (authorize/end_session), если
    #: отличается от OIDC_ISSUER. Внутри docker-сети приложение обращается к
    #: Keycloak по имени сервиса (`keycloak:8080`) — discovery и обмен кода
    #: на токен это имя резолвят; браузер оператора на хосте — нет, поэтому
    #: для редиректов подставляем этот адрес (например, `http://localhost:8080`).
    #: Пусто — поведение не меняется (адреса совпадают).
    oidc_public_issuer: str = ""
    oidc_client_id: str = "docs-app"
    oidc_client_secret: str = ""
    #: Внешний адрес приложения для redirect_uri.
    oidc_redirect_url: str = "http://localhost:8000/auth/callback"
    keycloak_admin_url: str = "http://keycloak:8080"
    keycloak_realm: str = "docs"
    keycloak_admin_user: str = ""
    keycloak_admin_password: str = ""

    #: Ключ подписи серверной сессии кабинета.
    session_secret: str = ""

    # --- хранилище и приём ------------------------------------------------- #
    storage_dir: Path = Path("./data/files")
    max_upload_mb: int = 20
    max_files_per_request: int = 30
    allowed_mime: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/tiff",
        "image/heic",
        "image/heif",
        "application/pdf",
    )

    # --- воркер ------------------------------------------------------------ #
    #: Сколько документов разбираем одновременно (asyncio-семафор, TZ 6).
    worker_concurrency: int = 4
    #: Пауза между опросами очереди, секунды.
    worker_poll_interval_s: float = 2.0
    #: Через сколько секунд `processing` считается зависшим и возвращается в `pending`.
    job_lock_timeout_s: int = 300
    #: Максимум попыток разбора одного документа.
    job_max_attempts: int = 3

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Кэшированные настройки (использовать как зависимость FastAPI)."""
    return Settings()


settings = get_settings()
