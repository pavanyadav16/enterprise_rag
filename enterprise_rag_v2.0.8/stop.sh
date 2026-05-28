#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — Stop (Linux / macOS)
# ./stop.sh           — stop, keep data
# ./stop.sh --reset   — stop + wipe all data
# =============================================================================
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./scripts/stop.sh "$@"
