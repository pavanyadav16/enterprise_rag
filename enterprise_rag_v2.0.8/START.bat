@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 — Quick Start (double-click me!)
:: Launches the PowerShell startup script with execution policy bypass.
:: Place this file in the PROJECT ROOT (same folder as docker-compose.yml).
:: =============================================================================
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\Start-RAG.ps1"
