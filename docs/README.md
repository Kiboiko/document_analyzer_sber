# Запуск с нуля

TODO(заполнять по ходу, не в конце — Д10).

## 1. Окружение
1. `cp .env.example .env` и заполнить значения (ключи — не в git).
2. `OLMOCR_SERVER` — адрес выданного vLLM с olmOCR, с суффиксом `/v1`.
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
TODO: стартовый админ Keycloak, как создать оператора из интерфейса.

## 5. Что должно получиться
TODO: сквозной сценарий — загрузка → фоновый разбор → карточка оператора.
