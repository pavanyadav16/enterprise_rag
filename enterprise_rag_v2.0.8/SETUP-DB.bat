@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 — External Database Setup (double-click me!)
:: Run this ONCE before START.bat to apply schemas to your SQL Server
:: and PostgreSQL/pgvector instances.
:: =============================================================================
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\Setup-ExternalDB.ps1"
