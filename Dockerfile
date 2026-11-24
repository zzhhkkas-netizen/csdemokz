FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Один воркер + потоки: разбор демки тяжёлый по памяти, параллельность ограничена MAX_PARALLEL.
# Таймаут большой: загрузка и разбор крупной демки идут одним запросом.
CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 900"]
