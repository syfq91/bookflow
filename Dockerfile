# syntax=docker/dockerfile:1

FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev --no-cache


FROM python:3.14-slim-bookworm

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY alembic.ini ./
COPY migrations ./migrations

RUN useradd --create-home --uid 1000 bookflow \
    && mkdir -p /app/data \
    && chown -R bookflow:bookflow /app \
    && python -c "import bookflow"

USER bookflow
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec gunicorn --bind 0.0.0.0:8000 --workers \"${GUNICORN_WORKERS:-2}\" bookflow.wsgi:app"]
