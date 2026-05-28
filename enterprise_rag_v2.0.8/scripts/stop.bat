@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 -- Stop Services (Windows CMD)
:: Usage:
::   scripts\stop.bat           Stop containers, keep all data
::   scripts\stop.bat /reset    Stop AND delete all Docker volumes (full reset)
::                              NOTE: your external SQL Server and PostgreSQL
::                              data are NOT affected by /reset
:: =============================================================================
setlocal EnableDelayedExpansion
title Enterprise RAG -- Stop Services

cd /d "%~dp0.."

echo.
echo ==============================================================
echo    Enterprise RAG v2.0.8 -- Stop Services
echo ==============================================================
echo.

set FULL_RESET=0
if /i "%~1"=="/reset"   set FULL_RESET=1
if /i "%~1"=="--reset"  set FULL_RESET=1

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is not running -- nothing to stop.
    pause
    exit /b 0
)

if "%FULL_RESET%"=="1" (
    echo *** FULL RESET -- All Docker volumes will be deleted! ***
    echo.
    echo This will permanently delete:
    echo   - Embedding model volume  (reload with scripts\load-model.bat^)
    echo   - Open WebUI data         (conversations, settings^)
    echo   - Uploaded source files
    echo.
    echo NOTE: Your external SQL Server and PostgreSQL data are NOT affected.
    echo.
    set /p CONFIRM="Type YES to confirm full reset: "
    if /i not "!CONFIRM!"=="YES" (
        echo Cancelled.
        pause
        exit /b 0
    )
    echo.
    echo Stopping and removing all containers and volumes...
    docker compose down -v --remove-orphans
    echo.
    echo Full reset complete. All Docker volumes deleted.
    echo Run START.bat to set up fresh.
) else (
    echo Stopping all containers (data volumes preserved^)...
    echo.
    docker compose down --remove-orphans
    echo.
    echo All services stopped. Data volumes preserved.
    echo.
    echo Restart     : START.bat
    echo Full reset  : scripts\stop.bat /reset
)

echo.
pause
endlocal
