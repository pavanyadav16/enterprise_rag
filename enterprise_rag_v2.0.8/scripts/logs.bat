@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 -- View Logs (Windows CMD)
:: Usage:
::   scripts\logs.bat                   Tail all services (last 100 lines each)
::   scripts\logs.bat backend           Tail one service
::   scripts\logs.bat backend 300       One service, last 300 lines
::   scripts\logs.bat --list            Show available service names
:: =============================================================================
setlocal
cd /d "%~dp0.."

set "SERVICE=%~1"
set "LINES=%~2"
if "%LINES%"=="" set LINES=100

echo.
echo ==============================================================
echo    Enterprise RAG v2.0.8 -- Log Viewer
echo ==============================================================
echo.

if /i "%SERVICE%"=="--list" (
    echo Available services:
    echo   nginx
    echo   backend
    echo   model-server
    echo   open-webui
    echo   owui-auth-proxy
    echo.
    goto :EOF
)

echo Press Ctrl+C to stop tailing.
echo.

if "%SERVICE%"=="" (
    echo Tailing ALL services (last %LINES% lines each^)...
    echo.
    docker compose logs -f --tail=%LINES%
) else (
    echo Tailing: %SERVICE% (last %LINES% lines^)...
    echo.
    docker compose logs -f --tail=%LINES% %SERVICE%
)

endlocal
