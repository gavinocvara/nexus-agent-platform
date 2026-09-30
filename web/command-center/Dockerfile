# NEXUS Command Center image (ADR 0013). Build context: the repository root.
#   docker compose --profile command-center build command-center
# PYTHON_IMAGE may name a base that already ships git (for example python:3.12) where the
# Debian mirrors are unreachable; the default slim base installs git from them.
ARG PYTHON_IMAGE=python:3.12-slim

# 1. Build the static client.
FROM node:22-slim AS client
WORKDIR /client
COPY web/command-center/package.json web/command-center/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/command-center/index.html web/command-center/vite.config.ts ./
COPY web/command-center/tsconfig.json web/command-center/tsconfig.app.json web/command-center/tsconfig.node.json ./
COPY web/command-center/src ./src
RUN npx vite build

# 2. Serve it with the read-only view layer. No Node at runtime.
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# git: replay re-executes the engineer evaluation catalog over fixture repositories.
RUN command -v git >/dev/null \
    || (apt-get update \
        && apt-get install --yes --no-install-recommends git \
        && rm -rf /var/lib/apt/lists/*)

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY lab ./lab

RUN python -m pip install --no-cache-dir . \
    && addgroup --system nexus \
    && adduser --system --ingroup nexus --home /tmp/nexus nexus

COPY --from=client /client/dist ./web/command-center/dist

USER nexus

EXPOSE 8765

CMD ["python", "-m", "nexus.command_center", "serve", "--host", "0.0.0.0", "--port", "8765"]
