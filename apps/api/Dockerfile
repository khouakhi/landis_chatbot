FROM python:3.12-slim-bookworm

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
COPY web ./web
COPY config ./config
COPY sql ./sql

RUN uv sync --package landis-api --frozen --no-dev

EXPOSE 8080

CMD ["sh", "-c", "cd /app && uv run --directory apps/api uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
