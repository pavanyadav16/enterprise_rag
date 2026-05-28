#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — External Database Setup Helper (Linux / macOS)
# Applies the SQL Server and PostgreSQL schemas to your external instances.
# Run this ONCE before starting the application for the first time.
#
# Requirements:
#   SQL Server : sqlcmd  (https://learn.microsoft.com/en-us/sql/tools/sqlcmd)
#   PostgreSQL : psql    (usually bundled with PostgreSQL client tools)
#
# Usage:
#   chmod +x scripts/setup-external-db.sh
#   ./scripts/setup-external-db.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

get_env() {
    local key="$1" default="${2:-}"
    grep -E "^\s*${key}\s*=" .env 2>/dev/null | grep -v "^\s*#" | head -1 | cut -d= -f2- | tr -d ' \r' || echo "$default"
}

echo ""
echo -e "${CYAN}=============================================================${RESET}"
echo -e "${BOLD}   Enterprise RAG v2.0.8 — External Database Setup${RESET}"
echo -e "${CYAN}=============================================================${RESET}"
echo ""

if [ ! -f ".env" ]; then
    echo -e "${RED}ERROR: .env file not found. Run ./start.sh first to create it.${RESET}"
    exit 1
fi

DB_HOST=$(get_env "DB_HOST")
DB_PORT=$(get_env "DB_PORT" "1433")
DB_NAME=$(get_env "DB_NAME" "EnterpriseRAG")
DB_USER=$(get_env "DB_USERNAME")
DB_PASS=$(get_env "DB_PASSWORD")
PG_HOST=$(get_env "PGVECTOR_HOST")
PG_PORT=$(get_env "PGVECTOR_PORT" "5432")
PG_DB=$(get_env "PGVECTOR_DB" "rag_vectors")
PG_USER=$(get_env "PGVECTOR_USER")
PG_PASS=$(get_env "PGVECTOR_PASSWORD")

echo -e "${BOLD}SQL Server:${RESET}  ${DB_HOST}:${DB_PORT}  db=${DB_NAME}"
echo -e "${BOLD}PostgreSQL:${RESET}  ${PG_HOST}:${PG_PORT}  db=${PG_DB}"
echo ""
read -rp "Apply schemas to these servers? (Y/n): " CONFIRM
[[ "${CONFIRM:-Y}" =~ ^[Nn] ]] && exit 0

# ── SQL Server schema ─────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}--- SQL Server ---${RESET}"
if ! command -v sqlcmd &>/dev/null; then
    echo -e "${YELLOW}sqlcmd not found. Apply sql/01_schema.sql manually using SSMS or Azure Data Studio.${RESET}"
    echo "  Server   : ${DB_HOST},${DB_PORT}"
    echo "  Database : ${DB_NAME}  (create it first if it doesn't exist)"
    echo "  Script   : $(pwd)/sql/01_schema.sql"
else
    echo "  Creating database ${DB_NAME} if it does not exist..."
    sqlcmd -S "${DB_HOST},${DB_PORT}" -U "$DB_USER" -P "$DB_PASS" \
        -Q "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='${DB_NAME}') CREATE DATABASE [${DB_NAME}];" \
        -b 2>&1

    echo "  Applying schema..."
    sqlcmd -S "${DB_HOST},${DB_PORT}" -U "$DB_USER" -P "$DB_PASS" \
        -d "$DB_NAME" -i sql/01_schema.sql -b 2>&1

    echo -e "${GREEN}  SQL Server schema applied.${RESET}"
fi

# ── PostgreSQL schema ─────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}--- PostgreSQL + pgvector ---${RESET}"
if ! command -v psql &>/dev/null; then
    echo -e "${YELLOW}psql not found. Apply sql/02_pgvector_schema.sql manually.${RESET}"
    echo "  Server   : ${PG_HOST}:${PG_PORT}"
    echo "  Database : ${PG_DB}  (create it first if it doesn't exist)"
    echo "  Script   : $(pwd)/sql/02_pgvector_schema.sql"
    echo ""
    echo "  Manual steps:"
    echo "    psql -h $PG_HOST -p $PG_PORT -U $PG_USER -c \"CREATE DATABASE ${PG_DB};\""
    echo "    psql -h $PG_HOST -p $PG_PORT -U $PG_USER -d $PG_DB -f sql/02_pgvector_schema.sql"
else
    echo "  Creating database ${PG_DB} if it does not exist..."
    PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d postgres \
        -c "SELECT 1 FROM pg_database WHERE datname='${PG_DB}'" | grep -q 1 || \
    PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d postgres \
        -c "CREATE DATABASE ${PG_DB};" 2>&1

    echo "  Applying schema..."
    PGPASSWORD="$PG_PASS" psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
        -f sql/02_pgvector_schema.sql 2>&1

    echo -e "${GREEN}  PostgreSQL schema applied.${RESET}"
fi

echo ""
echo -e "${GREEN}=============================================================${RESET}"
echo -e "${GREEN}  Database setup complete!${RESET}"
echo -e "${GREEN}  Run ./start.sh to start the application.${RESET}"
echo -e "${GREEN}=============================================================${RESET}"
echo ""
