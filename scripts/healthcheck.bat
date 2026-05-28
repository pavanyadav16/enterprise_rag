@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 -- Health Check (Windows CMD)
:: Checks external DB connectivity AND all Docker service endpoints.
::
:: Usage:
::   scripts\healthcheck.bat
::   scripts\healthcheck.bat http://myserver
:: =============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

set "BASE_URL=%~1"
if "%BASE_URL%"=="" set "BASE_URL=http://localhost"

set PASS=0
set FAIL=0
set SKIP=0

echo.
echo ==============================================================
echo    Enterprise RAG v2.0.8 -- Health Check
echo    Docker services  : %BASE_URL%
echo    External DBs     : read from .env
echo ==============================================================
echo.

where curl >nul 2>&1
if errorlevel 1 (
    echo ERROR: curl not found. Available on Windows 10 1803+.
    pause
    exit /b 1
)

goto :MAIN

:: ── HTTP check subroutine ─────────────────────────────────────────────────────
:HTTP_CHECK
set "HC_NAME=%~1"
set "HC_URL=%~2"
set "HC_EXPECTED=%~3"
if "%HC_EXPECTED%"=="" set "HC_EXPECTED=200"
for /f %%i in ('curl -s -o NUL -w "%%{http_code}" --max-time 5 "%HC_URL%" 2^>nul') do set "HC_CODE=%%i"
if "!HC_CODE!"=="%HC_EXPECTED%" (
    echo   PASS  !HC_NAME!  ^(!HC_CODE!^)
    set /a PASS+=1
) else (
    echo   FAIL  !HC_NAME!  ^(expected %HC_EXPECTED%, got !HC_CODE!^)
    echo         --^> %HC_URL%
    set /a FAIL+=1
)
goto :EOF

:: ── TCP check subroutine (uses PowerShell TcpClient) ─────────────────────────
:TCP_CHECK
set "TC_NAME=%~1"
set "TC_HOST=%~2"
set "TC_PORT=%~3"
powershell -NoProfile -Command ^
  "try{$t=New-Object Net.Sockets.TcpClient;$ar=$t.BeginConnect('%TC_HOST%',%TC_PORT%,$null,$null);$ok=$ar.AsyncWaitHandle.WaitOne(3000,$false);if($ok -and $t.Connected){$t.Close();exit 0}$t.Close();exit 1}catch{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo   FAIL  !TC_NAME!  ^(TCP %TC_HOST%:%TC_PORT% unreachable^)
    set /a FAIL+=1
) else (
    echo   PASS  !TC_NAME!  ^(TCP %TC_HOST%:%TC_PORT% reachable^)
    set /a PASS+=1
)
goto :EOF

:MAIN

:: ── External DB connectivity ──────────────────────────────────────────────────
echo External Databases:

set DB_HOST_VAL=
set DB_PORT_VAL=1433
set PG_HOST_VAL=
set PG_PORT_VAL=5432

if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="DB_HOST"       set DB_HOST_VAL=%%B
        if /i "%%A"=="DB_PORT"       set DB_PORT_VAL=%%B
        if /i "%%A"=="PGVECTOR_HOST" set PG_HOST_VAL=%%B
        if /i "%%A"=="PGVECTOR_PORT" set PG_PORT_VAL=%%B
    )
) else (
    echo   SKIP  .env not found -- cannot check external DB hosts
    set /a SKIP+=1
)

if not "!DB_HOST_VAL!"=="" (
    call :TCP_CHECK "SQL Server  (!DB_HOST_VAL!:!DB_PORT_VAL!)" "!DB_HOST_VAL!" "!DB_PORT_VAL!"
) else (
    echo   SKIP  SQL Server  ^(DB_HOST not set in .env^)
    set /a SKIP+=1
)

if not "!PG_HOST_VAL!"=="" (
    call :TCP_CHECK "PostgreSQL  (!PG_HOST_VAL!:!PG_PORT_VAL!)" "!PG_HOST_VAL!" "!PG_PORT_VAL!"
) else (
    echo   SKIP  PostgreSQL  ^(PGVECTOR_HOST not set in .env^)
    set /a SKIP+=1
)

:: ── Docker service endpoints ──────────────────────────────────────────────────
echo.
echo Docker Services:
call :HTTP_CHECK "Nginx liveness             " "%BASE_URL%/api/v1/health/live"    "200"
call :HTTP_CHECK "Nginx readiness            " "%BASE_URL%/api/v1/health/ready"   "200"
call :HTTP_CHECK "Backend health (full)      " "%BASE_URL%/api/v1/health"         "200"
call :HTTP_CHECK "Auth proxy health          " "%BASE_URL%/auth-proxy/health"     "200"
call :HTTP_CHECK "Open WebUI root            " "%BASE_URL%/"                      "200"
call :HTTP_CHECK "API Swagger docs           " "%BASE_URL%/api/docs"              "200"
call :HTTP_CHECK "Chat models (no JWT = 401) " "%BASE_URL%/api/v1/chat/models"   "401"

:: ── Container status ──────────────────────────────────────────────────────────
echo.
echo Container Status:
docker compose ps 2>nul || echo   (docker compose not available^)

:: ── Summary ───────────────────────────────────────────────────────────────────
echo.
echo ==============================================================
if %FAIL%==0 (
    echo   All checks PASSED  ^(%PASS% passed, %SKIP% skipped^)
) else (
    echo   Results: %PASS% passed, %FAIL% failed, %SKIP% skipped
    echo.
    echo   Troubleshooting:
    echo     docker compose ps              ^(check container status^)
    echo     docker compose logs backend    ^(check backend errors^)
    echo     scripts\logs.bat               ^(tail all service logs^)
    echo     notepad .env                   ^(verify DB_HOST / PGVECTOR_HOST^)
)
echo ==============================================================
echo.
pause
endlocal
