# Переход на локальную модель

Требование Д6: модель изолирована, провайдер берётся из конфига, локальный
вариант рабочий и к нему есть инструкция. Качество распознавания локальной
моделью не оценивается — важна работоспособность.

## Что где живёт

Все обращения к модели идут через один слой — `app/model/`. Больше нигде в
коде модель не вызывается, поэтому переезд на локальный вариант не требует
правок за пределами `.env`.

```
app/model/__init__.py       recognize(doc_type, image_bytes) -> ExtractResult
app/model/ocr.py            шаг 1: olmOCR (self-hosted) -> текст
app/model/providers/cloud.py  шаг 2: Qwen через ilons API
app/model/providers/local.py  шаг 2: Ollama на своём железе
app/model/prompts/*.txt     промпты, по одному на тип документа
app/model/validate.py       нормализация и правила проверки полей
```

Разбор всегда двухшаговый:

1. **OCR** — `olmOCR` на выданном сервере vLLM превращает фото в текст.
2. **LLM** — по промпту `prompts/<doc_type>.txt` извлекает из этого текста
   JSON с полями.

Локальный вариант заменяет **второй** шаг. OCR остаётся self-hosted в обоих
случаях, поэтому «переезд на собственную модель» означает: убрать последнюю
зависимость от внешнего API.

## Переключение

Выбор провайдера — переменная `MODEL_PROVIDER`, значения `cloud` или `local`.
Читается в `app/model/get_provider()` при каждом разборе, константы в коде нет.

### 1. Поднять Ollama

```bash
docker compose --profile ollama up -d
```

### 2. Загрузить модель

```bash
docker compose exec ollama ollama pull qwen2.5vl:3b
docker compose exec ollama ollama list      # убедиться, что модель на месте
```

Модель можно взять любую — важно, чтобы она умела следовать инструкции
«верни JSON». Имя задаётся переменной `LOCAL_VLM_MODEL` и должно в точности
совпадать с тем, что печатает `ollama list`.

### 3. Переписать `.env`

```
MODEL_PROVIDER=local
OLLAMA_HOST=http://ollama:11434
LOCAL_VLM_MODEL=qwen2.5vl:3b
```

`OLMOCR_SERVER` и `OLMOCR_MODEL` не трогаем: OCR и так свой.

### 4. Перезапустить приложение и воркер

```bash
docker compose restart app worker
```

Миграций и пересборки образа не нужно — поменялось только окружение.

### 5. Проверить

```bash
curl http://localhost:8000/health
# -> {"status":"ok", ..., "model_provider":"local"}
```

Затем загрузить документ через страницу соискателя и дождаться статуса
`готово`.

## Как убедиться, что переключение реально работает

`/health` показывает только то, что написано в конфиге. Факт обращения именно
к локальной модели виден по `ExtractResult.provider`: модельный слой возвращает
туда имя сработавшего провайдера.

Договорённость с воркером (`worker.process_job`): это значение попадает в
`payload` события `parse_done`. Тогда подмену провайдера видно в истории
заявки, а не только в переменной окружения:

```bash
docker compose exec postgres psql -U app -d app -c \
  "select type, payload->>'provider' as provider, created_at
     from events where type = 'parse_done' order by created_at desc limit 5;"
```

## Если что-то не так

| Симптом | Причина | Что делать |
|---|---|---|
| `Ollama /api/chat: ...Connection refused` | контейнер не поднят | `docker compose --profile ollama up -d` |
| `model "..." not found` | имя в `LOCAL_VLM_MODEL` не совпадает | сверить с `ollama list` |
| `в ответе модели нет JSON-объекта` | модель слишком мелкая, не держит формат | взять модель крупнее; `format: json` уже включён в запросе |
| `olmOCR недоступен` | не задан или недоступен `OLMOCR_SERVER` | проверить адрес, он должен быть с суффиксом `/v1` |
| разбор очень долгий | модель считается на CPU | уменьшить `WORKER_CONCURRENCY`, взять модель меньше |

Ошибка провайдера не роняет воркер: задача уходит в retry, документ получает
понятный статус, выдуманные поля в БД не попадают.

## Сверка имени облачной модели

Имя модели в `LLM_MODEL` легко разъезжается с тем, что реально отдаёт шлюз.
Проверить:

```bash
curl -s -H "Authorization: Bearer $ILONS_API_KEY" \
  https://llm.ilons.ru/v1/models | python -m json.tool
```

То же самое делает `app.model.providers.cloud.list_models()`.
