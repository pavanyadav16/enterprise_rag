@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 — Stop (double-click me!)
:: =============================================================================
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\Stop-RAG.ps1"
