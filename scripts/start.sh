#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — Linux / macOS Startup Script
# SQL Server and PostgreSQL are EXTERNAL — not started by this script.
# Dockerised: model-server, backend, open-webui, owui-auth-proxy, nginx
#
# Usage:
#   chmod +x scripts/start.sh && ./scripts/start.sh
#   ./scripts/start.sh --skip-build       (skip docker compose build)
#   ./scripts/start.sh --skip-model       (skip model volume check)
#   ./scripts/start.sh --logs             (tail logs after start)
#   ./scripts/start.sh --down             (stop all services)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

SKIP_BUILD=0; SKIP_MODEL=0; TAIL_LOGS=0; DO_DOWN=0
for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=1 ;;
        --skip-model) SKIP_MODEL=1 ;;
        --logs)       TAIL_LOGS=1  ;;
        --down)       DO_DOWN=1    ;;
        -h|--help)
            echo "Usage: $0 [--skip-build] [--skip-model] [--logs] [--down]"
            exit 0 ;;
        *) echo "Unknown flag: $arg"; exit 1 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

step()  { echo -e "\n${CYAN}[$1] $2${RESET}"; }
ok()    { echo -e "  ${GREEN}OK${RESET}    $1"; }
warn()  { echo -e "  ${YELLOW}WARN${RESET}  $1"; }
err()   { echo -e "  ${RED}ERR${RESET}   $1"; }
info()  { echo -e "        $1"; }

# Parse a value from .env (no external tools needed)
get_env() {
    local key="$1" default="${2:-}"
    local val
    val=$(grep -E "^\s*${key}\s*=" .env 2>/dev/null | grep -v "^\s*#" | head -1 | cut -d= -f2- | tr -d ' \r')
    echo "${val:-$default}"
}

# TCP reachability check using /dev/tcp (bash built-in, no nc/telnet needed)
tcp_check() {
    local host="$1" port="$2"
    timeout 3 bash -c "echo > /dev/tcp/${host}/${port}" 2>/dev/null
}

echo ""
echo -e "${CYAN}=============================================================${RESET}"
echo -e "${BOLD}   Enterprise RAG v2.0.8 — Linux/macOS Startup${RESET}"
echo -e "${GRAY:-}   Dockerised : model-server, backend, open-webui, nginx${RESET}"
echo -e "   External   : SQL Server, PostgreSQL+pgvector (your servers)"
echo -e "${CYAN}=============================================================${RESET}"

# ── Stop mode ────────────────────────────────────────────────────────────────
if [ "$DO_DOWN" -eq 1 ]; then
    echo -e "\n${YELLOW}Stopping all services...${RESET}"
    docker compose down
    echo -e "${GREEN}Stopped. Data volumes preserved.${RESET}"
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Docker
# ─────────────────────────────────────────────────────────────────────────────
step "1/7" "Checking Docker..."
if ! command -v docker &>/dev/null; then
    err "Docker not installed."
    info "Linux : curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker \$USER"
    info "macOS : https://www.docker.com/products/docker-desktop/"
    exit 1
fi
if ! docker info &>/dev/null; then
    err "Docker daemon not running."
    info "Linux : sudo systemctl start docker"
    info "macOS : Open Docker Desktop from Applications"
    exit 1
fi
ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Docker Compose v2
# ─────────────────────────────────────────────────────────────────────────────
step "2/7" "Checking Docker Compose v2..."
if ! docker compose version &>/dev/null; then
    err "Docker Compose v2 not found."
    info "Ubuntu/Debian : sudo apt-get install docker-compose-plugin"
    info "RHEL/CentOS   : sudo yum install docker-compose-plugin"
    exit 1
fi
ok "$(docker compose version --short 2>/dev/null || echo 'v2')"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — .env file
# ─────────────────────────────────────────────────────────────────────────────
step "3/7" "Checking .env configuration..."
if [ ! -f ".env" ]; then
    [ ! -f ".env.example" ] && { err ".env.example not found. Run from project root."; exit 1; }
    cp ".env.example" ".env"
    warn ".env created — ACTION REQUIRED"
    info ""
    info "Fill in .env:"
    info "  DB_HOST, DB_PORT, DB_NAME, DB_USERNAME, DB_PASSWORD"
    info "  PGVECTOR_HOST, PGVECTOR_PORT, PGVECTOR_DB, PGVECTOR_USER, PGVECTOR_PASSWORD"
    info "  LLM_TOKEN_URL, LLM_GENERATE_URL, LLM_USERNAME, LLM_PASSWORD"
    info "  OWUI_ADMIN_EMAIL, OWUI_ADMIN_PASSWORD, OWUI_SECRET_KEY, OWUI_AUTO_LOGIN_SECRET"

    if command -v openssl &>/dev/null; then
        read -rp "  Auto-generate OWUI secrets? (Y/n): " GEN
        if [[ "${GEN:-Y}" =~ ^[Yy] ]]; then
            S1=$(openssl rand -hex 32); S2=$(openssl rand -hex 32)
            sed -i.bak "s|OWUI_SECRET_KEY=.*|OWUI_SECRET_KEY=${S1}|" .env
            sed -i.bak "s|OWUI_AUTO_LOGIN_SECRET=.*|OWUI_AUTO_LOGIN_SECRET=${S2}|" .env
            rm -f .env.bak
            ok "Secrets generated"
        fi
    fi

    EDITOR_CMD="${EDITOR:-${VISUAL:-nano}}"
    read -rp "  Open .env in $EDITOR_CMD? (Y/n): " EDIT
    [[ "${EDIT:-Y}" =~ ^[Yy] ]] && $EDITOR_CMD .env

    read -rp "  Continue startup? (Y/n): " CONT
    [[ "${CONT:-Y}" =~ ^[Nn] ]] && { echo "Edit .env then run: ./start.sh"; exit 0; }
else
    ok ".env file exists"
fi

set -a; source .env 2>/dev/null || true; set +a

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — External DB connectivity
# ─────────────────────────────────────────────────────────────────────────────
step "4/7" "Checking external database connectivity..."
echo ""

DB_HOST_VAL=$(get_env "DB_HOST")
DB_PORT_VAL=$(get_env "DB_PORT" "1433")
PG_HOST_VAL=$(get_env "PGVECTOR_HOST")
PG_PORT_VAL=$(get_env "PGVECTOR_PORT" "5432")

# SQL Server
if [ -z "$DB_HOST_VAL" ] || [[ "$DB_HOST_VAL" == *"your-"* ]]; then
    warn "DB_HOST not set in .env — skipping SQL Server check"
else
    info "  SQL Server  : ${DB_HOST_VAL}:${DB_PORT_VAL}"
    if tcp_check "$DB_HOST_VAL" "$DB_PORT_VAL"; then
        ok "SQL Server reachable"
    else
        err "Cannot reach SQL Server at ${DB_HOST_VAL}:${DB_PORT_VAL}"
        info ""
        info "  Check:"
        info "    1. DB_HOST and DB_PORT in .env are correct"
        info "    2. SQL Server is running and TCP/IP is enabled"
        info "    3. Firewall allows port $DB_PORT_VAL from this host"
        info "    4. Run sql/01_schema.sql to create the EnterpriseRAG database"
        info ""
        read -rp "  Continue anyway? (Y/n): " CONT
        [[ "${CONT:-Y}" =~ ^[Nn] ]] && exit 1
    fi
fi

echo ""

# PostgreSQL
if [ -z "$PG_HOST_VAL" ] || [[ "$PG_HOST_VAL" == *"your-"* ]]; then
    warn "PGVECTOR_HOST not set in .env — skipping PostgreSQL check"
else
    info "  PostgreSQL  : ${PG_HOST_VAL}:${PG_PORT_VAL}"
    if tcp_check "$PG_HOST_VAL" "$PG_PORT_VAL"; then
        ok "PostgreSQL reachable"
    else
        err "Cannot reach PostgreSQL at ${PG_HOST_VAL}:${PG_PORT_VAL}"
        info ""
        info "  Check:"
        info "    1. PGVECTOR_HOST and PGVECTOR_PORT in .env are correct"
        info "    2. PostgreSQL is running and listening on that address"
        info "    3. pg_hba.conf allows connections from this host"
        info "    4. pgvector extension: psql -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
        info "    5. Schema applied: run sql/02_pgvector_schema.sql"
        info ""
        read -rp "  Continue anyway? (Y/n): " CONT
        [[ "${CONT:-Y}" =~ ^[Nn] ]] && exit 1
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Auth mode
# ─────────────────────────────────────────────────────────────────────────────
step "5/7" "Checking authentication mode..."
DEV_MODE=$(get_env "APP_DEV_MODE" "false")
if [ "$DEV_MODE" = "true" ]; then
    warn "DEV MODE ON — JWT verification disabled. Local testing only."
else
    if [ ! -f "backend/conf/jwt_public_key.pem" ]; then
        warn "JWT public key missing: backend/conf/jwt_public_key.pem"
        info "For dev: set APP_DEV_MODE=true in .env"
        info "For prod:"
        info "  openssl genrsa -out backend/conf/jwt_private_key.pem 2048"
        info "  openssl rsa -in backend/conf/jwt_private_key.pem -pubout \\"
        info "      -out backend/conf/jwt_public_key.pem"
        read -rp "  Continue anyway? (Y/n): " CONT
        [[ "${CONT:-Y}" =~ ^[Nn] ]] && exit 0
    else
        ok "JWT public key found"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Embedding model
# ─────────────────────────────────────────────────────────────────────────────
step "6/7" "Checking embedding model..."
if [ "$SKIP_MODEL" -eq 1 ]; then
    warn "Model check skipped (--skip-model)"
else
    MODEL_CHECK=$(docker run --rm \
        -v "enterprise_rag_v200_model_weights:/models" \
        alpine sh -c "test -f /models/config.json && echo FOUND || echo EMPTY" 2>/dev/null || echo "EMPTY")

    if [ "$MODEL_CHECK" = "FOUND" ]; then
        ok "Embedding model already in Docker volume"
    else
        warn "Model not found in Docker volume"
        MODEL_SRC=$(get_env "MODEL_PATH" "")
        if [ -n "$MODEL_SRC" ] && [ -d "$MODEL_SRC" ]; then
            read -rp "  Load from MODEL_PATH ($MODEL_SRC)? (Y/n): " USE_ENV
            [[ "${USE_ENV:-Y}" =~ ^[Nn] ]] && MODEL_SRC=""
        else
            MODEL_SRC=""
        fi
        [ -z "$MODEL_SRC" ] && read -rp "  Path to model directory (Enter to skip): " MODEL_SRC

        if [ -n "$MODEL_SRC" ] && [ -d "$MODEL_SRC" ]; then
            ABS_SRC="$(cd "$MODEL_SRC" && pwd)"
            info "  Loading from: $ABS_SRC"
            docker run --rm \
                -v "${ABS_SRC}:/src:ro" \
                -v "enterprise_rag_v200_model_weights:/models" \
                alpine sh -c "cp -r /src/. /models/ && echo 'Model loaded OK'"
            [ $? -eq 0 ] && ok "Model loaded" || err "Copy failed. Run ./scripts/load-model.sh manually."
        elif [ -n "$MODEL_SRC" ]; then
            err "Path not found: $MODEL_SRC"
        else
            warn "Skipped. Run: ./scripts/load-model.sh /path/to/model"
        fi
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — Build and start
# ─────────────────────────────────────────────────────────────────────────────
step "7/7" "Building and starting Docker services..."
info "  Services: model-server, backend, open-webui, owui-auth-proxy, nginx"
echo ""

if [ "$SKIP_BUILD" -eq 0 ]; then
    info "  Building (first: 5-15 min, cached: ~1-2 min)..."
    docker compose build
    ok "Images built"
    echo ""
fi

docker compose up -d

# ── Wait for health ──────────────────────────────────────────────────────────
echo ""
echo -e "  ${CYAN}Waiting for services to become healthy...${RESET}"
info "  (Model-server takes up to 90 s on first boot)"

MAX_WAIT=150; INTERVAL=5; ELAPSED=0; READY=0
while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    sleep "$INTERVAL"; ELAPSED=$((ELAPSED+INTERVAL))
    CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
        http://localhost/api/v1/health/live 2>/dev/null || echo "000")
    [ "$CODE" = "200" ] && { READY=1; break; }
    PCT=$((ELAPSED*100/MAX_WAIT))
    info "  ${ELAPSED}s / ${MAX_WAIT}s  (${PCT}%)"
done

echo ""
docker compose ps
echo ""

if [ "$READY" -eq 1 ]; then
    echo -e "${GREEN}=============================================================${RESET}"
    echo -e "${GREEN}${BOLD}   Enterprise RAG v2.0.8 is READY!${RESET}"
    echo -e "${GREEN}=============================================================${RESET}"
else
    echo -e "${YELLOW}=============================================================${RESET}"
    echo -e "${YELLOW}   Services started — some may still be initialising.${RESET}"
    echo -e "${YELLOW}   Watch: docker compose logs -f${RESET}"
    echo -e "${YELLOW}=============================================================${RESET}"
fi

echo ""
echo -e "${BOLD}  Access Points:${RESET}"
echo -e "    Auto-login  : ${CYAN}http://localhost/?token=<your_jwt>${RESET}"
echo -e "    Open WebUI  : ${CYAN}http://localhost/${RESET}"
echo -e "    API Docs    : ${CYAN}http://localhost/api/docs${RESET}"
echo -e "    Health      : ${CYAN}http://localhost/api/v1/health${RESET}"
echo ""
echo -e "${BOLD}  Commands:${RESET}"
echo "    All logs    : docker compose logs -f"
echo "    Backend log : docker compose logs -f backend"
echo "    Stop        : ./scripts/stop.sh"
echo ""

[ "$TAIL_LOGS" -eq 1 ] && docker compose logs -f
