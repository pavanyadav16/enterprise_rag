#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — Stop Services (Linux / macOS)
# Usage:
#   ./scripts/stop.sh           Stop containers, keep all data
#   ./scripts/stop.sh --reset   Stop AND delete all volumes (full reset)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

FULL_RESET=0
[[ "${1:-}" == "--reset" ]] && FULL_RESET=1

echo ""
echo -e "${CYAN}=============================================================${RESET}"
echo -e "${BOLD}   Enterprise RAG v2.0.8 — Stop Services${RESET}"
echo -e "${CYAN}=============================================================${RESET}"
echo ""

if ! docker info &>/dev/null; then
    echo -e "${YELLOW}  Docker is not running — nothing to stop.${RESET}"
    exit 0
fi

if [ "$FULL_RESET" -eq 1 ]; then
    echo -e "${RED}  *** FULL RESET — All data volumes will be deleted! ***${RESET}"
    echo ""
    echo -e "${YELLOW}  This permanently deletes:${RESET}"
    echo "    - SQL Server database (users, roles, sources, chat history)"
    echo "    - PGVector embeddings (all indexed document chunks)"
    echo "    - Open WebUI data (conversations, user settings)"
    echo "    - Uploaded source files"
    echo ""
    read -rp "  Type YES to confirm: " CONFIRM
    if [ "$CONFIRM" != "YES" ]; then
        echo "  Cancelled."
        exit 0
    fi
    echo ""
    echo -e "${YELLOW}  Removing all containers and volumes...${RESET}"
    docker compose down -v --remove-orphans
    echo ""
    echo -e "${GREEN}  Full reset complete. All data deleted.${RESET}"
    echo "  Run ./scripts/start.sh to set up fresh."
else
    echo -e "${CYAN}  Stopping all containers (data volumes preserved)...${RESET}"
    docker compose down --remove-orphans
    echo ""
    echo -e "${GREEN}  All services stopped. Data volumes preserved.${RESET}"
    echo "  Restart  : ./scripts/start.sh --skip-build"
    echo "  Full reset: ./scripts/stop.sh --reset"
fi
echo ""
