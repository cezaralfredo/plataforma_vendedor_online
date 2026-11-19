#!/bin/bash
# Entrypoint para produção - roda migrações e inicia a API

set -e

load_secret() {
  local variable_name="$1"
  local file_variable_name="${variable_name}_FILE"
  local file_path="${!file_variable_name:-}"

  if [ -n "$file_path" ] && [ -f "$file_path" ]; then
    export "$variable_name=$(tr -d '\r\n' < "$file_path")"
  fi
}

for secret in DATABASE_URL POSTGRES_PASSWORD API_KEY ADMIN_API_KEY ADMIN_USERNAME ADMIN_BOOTSTRAP_PASSWORD ADMIN_SESSION_SECRET ADMIN_RECOVERY_KEY ASAAS_API_KEY ASAAS_WEBHOOK_TOKEN; do
  load_secret "$secret"
done

if [ -n "${POSTGRES_PASSWORD:-}" ]; then
  export DATABASE_URL="${DATABASE_URL/__POSTGRES_PASSWORD__/$POSTGRES_PASSWORD}"
fi

echo "🚀 Iniciando container da API..."

# Rodar migrações Alembic
echo "🔄 Executando migrações Alembic..."
python /app/scripts/run_migrations.py
echo "✅ Migrações aplicadas"

# Iniciar aplicação
echo "🌐 Iniciando Uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${WEB_CONCURRENCY:-2}"
