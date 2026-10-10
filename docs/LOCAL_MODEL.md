# Переход на локальную модель

TODO: пошагово (Д6). Качество на защите не оценивается — важна работоспособность.

1. `docker compose --profile ollama up -d`
2. `docker compose exec ollama ollama pull <LOCAL_VLM_MODEL>`
3. В `.env`: `MODEL_PROVIDER=local`, `OLLAMA_HOST=...`, `LOCAL_VLM_MODEL=...`
4. `docker compose restart app worker`
5. Проверка: загрузить документ, увидеть `provider=local` в разборе.

OCR остаётся self-hosted (olmOCR) в обоих вариантах; локально заменяется
только LLM — это и есть полный on-prem.
