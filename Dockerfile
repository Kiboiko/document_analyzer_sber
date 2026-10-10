FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Зависимости отдельным слоем — чтобы не пересобирать на каждую правку кода.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Каталог хранилища файлов монтируется томом (см. docker-compose.yml).
RUN mkdir -p /srv/data/files

EXPOSE 8000

# Воркер переопределяет command на `python -m app.worker`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
