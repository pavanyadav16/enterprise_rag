#!/usr/bin/env bash
# =============================================================================
# Enterprise RAG v2.0.8 — Quick Start (Linux / macOS)
# Run this from the project root: ./start.sh
# Forwards all arguments to scripts/start.sh
# =============================================================================
cd "$(dirname "${BASH_SOURCE[0]}")"
exec ./scripts/start.sh "$@"
