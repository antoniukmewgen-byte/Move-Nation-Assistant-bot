FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home /app app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data logs && chown -R app:app /app

USER app

EXPOSE 8000

# Applies pending Alembic migrations, then starts the bot + API + scheduler
# in a single process (see app/main.py for why they share one event loop).
CMD ["sh", "-c", "python -m alembic upgrade head && python -m app.main"]
