#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — Log Viewer (Linux / macOS)
# Tails Docker service logs with colour-coded service names.
#
# Usage:
#   ./scripts/logs.sh                  # all services, last 100 lines
#   ./scripts/logs.sh backend          # one service
#   ./scripts/logs.sh backend 300      # one service, last 300 lines
#   ./scripts/logs.sh --list           # show available service names
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

SERVICE="${1:-}"
LINES="${2:-100}"

CYAN='\033[0;36m'; BOLD='\033[1m'; YELLOW='\033[1;33m'; RESET='\033[0m'

SERVICES="nginx backend model-server open-webui owui-auth-proxy"

if [ "$SERVICE" = "--list" ]; then
    echo ""
    echo -e "${BOLD}Available services:${RESET}"
    for s in $SERVICES; do echo "  $s"; done
    echo ""
    exit 0
fi

if ! docker info &>/dev/null; then
    echo -e "${YELLOW}Docker is not running — no logs available.${RESET}"
    exit 1
fi

echo ""
echo -e "${CYAN}=============================================================${RESET}"
if [ -z "$SERVICE" ]; then
    echo -e "${BOLD}   Enterprise RAG — All Service Logs (last $LINES lines each)${RESET}"
    echo -e "${CYAN}=============================================================${RESET}"
    echo "  Services: $SERVICES"
else
    echo -e "${BOLD}   Enterprise RAG — $SERVICE logs (last $LINES lines)${RESET}"
    echo -e "${CYAN}=============================================================${RESET}"
fi
echo "  Press Ctrl+C to stop tailing."
echo ""

if [ -z "$SERVICE" ]; then
    docker compose logs -f --tail="$LINES"
else
    docker compose logs -f --tail="$LINES" "$SERVICE"
fi
