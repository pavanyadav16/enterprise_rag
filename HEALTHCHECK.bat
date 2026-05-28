@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 — Health Check (double-click me!)
:: =============================================================================
cd /d "%~dp0"
call scripts\healthcheck.bat http://localhost
