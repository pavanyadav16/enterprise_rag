#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — Health Check (Linux / macOS)
# Checks all Docker service endpoints AND external DB connectivity.
#
# Usage:
#   ./scripts/healthcheck.sh                  # check http://localhost
#   ./scripts/healthcheck.sh http://myserver  # check a remote host
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

BASE_URL="${1:-http://localhost}"
PASS=0; FAIL=0; WARN_COUNT=0

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

http_check() {
    local name="$1" url="$2" expected="${3:-200}"
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
    if [ "$code" = "$expected" ]; then
        echo -e "  ${GREEN}PASS${RESET}  $name  ($code)"
        PASS=$((PASS+1))
    else
        echo -e "  ${RED}FAIL${RESET}  $name  (expected $expected, got $code)  → $url"
        FAIL=$((FAIL+1))
    fi
}

tcp_check() {
    local name="$1" host="$2" port="$3"
    if timeout 3 bash -c "echo > /dev/tcp/${host}/${port}" 2>/dev/null; then
        echo -e "  ${GREEN}PASS${RESET}  $name  (TCP ${host}:${port} reachable)"
        PASS=$((PASS+1))
    else
        echo -e "  ${RED}FAIL${RESET}  $name  (TCP ${host}:${port} unreachable)"
        FAIL=$((FAIL+1))
    fi
}

get_env() {
    local key="$1" default="${2:-}"
    grep -E "^\s*${key}\s*=" .env 2>/dev/null | grep -v "^\s*#" | head -1 \
        | cut -d= -f2- | tr -d ' \r' || echo "$default"
}

echo ""
echo -e "${CYAN}=============================================================${RESET}"
echo -e "${BOLD}   Enterprise RAG v2.0.8 — Health Check${RESET}"
echo -e "${CYAN}   Docker services  : $BASE_URL${RESET}"
echo -e "${CYAN}   External DBs     : read from .env${RESET}"
echo -e "${CYAN}=============================================================${RESET}"
echo ""

# ── External DB connectivity ──────────────────────────────────────────────────
echo -e "${BOLD}  External Databases:${RESET}"

if [ -f ".env" ]; then
    DB_HOST=$(get_env "DB_HOST")
    DB_PORT=$(get_env "DB_PORT" "1433")
    PG_HOST=$(get_env "PGVECTOR_HOST")
    PG_PORT=$(get_env "PGVECTOR_PORT" "5432")

    if [ -z "$DB_HOST" ] || [[ "$DB_HOST" == *"your-"* ]] || [[ "$DB_HOST" == *"192.168"* && "$DB_HOST" == "192.168.1.100" ]]; then
        echo -e "  ${YELLOW}SKIP${RESET}  SQL Server  (DB_HOST not configured in .env)"
        WARN_COUNT=$((WARN_COUNT+1))
    else
        tcp_check "SQL Server  (${DB_HOST}:${DB_PORT})" "$DB_HOST" "$DB_PORT"
    fi

    if [ -z "$PG_HOST" ] || [[ "$PG_HOST" == *"your-"* ]] || [[ "$PG_HOST" == "192.168.1.101" ]]; then
        echo -e "  ${YELLOW}SKIP${RESET}  PostgreSQL  (PGVECTOR_HOST not configured in .env)"
        WARN_COUNT=$((WARN_COUNT+1))
    else
        tcp_check "PostgreSQL  (${PG_HOST}:${PG_PORT})" "$PG_HOST" "$PG_PORT"
    fi
else
    echo -e "  ${YELLOW}SKIP${RESET}  .env not found — cannot check external DB hosts"
    WARN_COUNT=$((WARN_COUNT+1))
fi

# ── Docker service endpoints ──────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Docker Services:${RESET}"
http_check "Nginx liveness             " "$BASE_URL/api/v1/health/live"
http_check "Nginx readiness            " "$BASE_URL/api/v1/health/ready"
http_check "Backend health (full)      " "$BASE_URL/api/v1/health"
http_check "Auth proxy health          " "$BASE_URL/auth-proxy/health"
http_check "Open WebUI root            " "$BASE_URL/"
http_check "API Swagger docs           " "$BASE_URL/api/docs"
http_check "Chat models (no JWT → 401) " "$BASE_URL/api/v1/chat/models" 401

# ── Container status ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Container Status:${RESET}"
if docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null; then
    : # printed inline
else
    echo "  (docker compose not available or not in project root)"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}=============================================================${RESET}"
TOTAL=$((PASS + FAIL))
if [ "$FAIL" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}  All $TOTAL checks PASSED${RESET}"
    [ "$WARN_COUNT" -gt 0 ] && echo -e "${YELLOW}  ($WARN_COUNT skipped — configure .env to check external DBs)${RESET}"
else
    echo -e "${BOLD}  Results: ${GREEN}$PASS passed${RESET}, ${RED}$FAIL failed${RESET}, ${YELLOW}$WARN_COUNT skipped${RESET}"
    echo ""
    echo "  Troubleshooting:"
    echo "    docker compose ps              — check container status"
    echo "    docker compose logs backend    — check backend errors"
    echo "    ./scripts/logs.sh              — tail all service logs"
    echo "    cat .env                       — verify DB_HOST / PGVECTOR_HOST"
fi
echo -e "${CYAN}=============================================================${RESET}"
echo ""

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
