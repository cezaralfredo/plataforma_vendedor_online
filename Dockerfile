# =============================================================================
# Multi-stage Dockerfile para Plataforma Atende Agenda
# =============================================================================

# ---- Base Stage ----
FROM node:22-alpine AS assets
WORKDIR /src
COPY package.json package-lock.json tailwind.config.js ./
RUN npm ci
COPY app/admin ./app/admin
COPY scripts/copy-admin-icons.cjs ./scripts/copy-admin-icons.cjs
RUN npm run build:admin

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- Dependencies Stage ----
FROM base AS deps

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Build Stage ----
FROM deps AS build

COPY . .
# Remover arquivos desnecessários da imagem final
RUN find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true \
    && find . -type f -name "*.pyc" -delete \
    && find . -type f -name "*.pyo" -delete \
    && find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true \
    && rm -rf .git .pytest_cache tests/ hermes/ alembic/versions/*.pyc .env* README.md plano-de-implementacao.md

# ---- Runtime Stage ----
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Instalar apenas runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

# Copiar dependências instaladas do stage deps
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

# Copiar código da aplicação
COPY --from=build --chown=appuser:appgroup /app .
COPY --from=assets --chown=appuser:appgroup /src/app/admin/static/admin.css ./app/admin/static/admin.css
COPY --from=assets --chown=appuser:appgroup /src/app/admin/static/vendor/fontawesome ./app/admin/static/vendor/fontawesome
RUN test -f /app/scripts/run_migrations.py

# Copiar entrypoint
COPY --chown=appuser:appgroup entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Criar diretórios necessários
RUN mkdir -p /app/alembic/versions && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f -s http://127.0.0.1:8000/ready || python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
