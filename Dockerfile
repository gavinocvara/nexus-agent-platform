FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY lab ./lab
COPY alembic.ini ./
COPY migrations ./migrations

RUN python -m pip install --no-cache-dir . \
    && addgroup --system nexus \
    && adduser --system --ingroup nexus nexus \
    && chown -R nexus:nexus /app

USER nexus

EXPOSE 8000

CMD ["uvicorn", "nexus.services.gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
