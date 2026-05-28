@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 -- Windows Startup Script (CMD)
:: SQL Server and PostgreSQL are EXTERNAL -- not started by this script.
:: Starts: model-server, backend, open-webui, owui-auth-proxy, nginx
::
:: Usage:
::   scripts\start.bat
::   scripts\start.bat /skipbuild
:: =============================================================================
setlocal EnableDelayedExpansion
title Enterprise RAG v2.0.8 -- Startup

cd /d "%~dp0.."

set SKIP_BUILD=0
if /i "%~1"=="/skipbuild"   set SKIP_BUILD=1
if /i "%~1"=="--skip-build" set SKIP_BUILD=1

echo.
echo ==============================================================
echo    Enterprise RAG v2.0.8 -- Windows Startup
echo ==============================================================
echo    Dockerised : model-server, backend, open-webui, nginx
echo    External   : SQL Server, PostgreSQL+pgvector (YOUR servers)
echo ==============================================================
echo.

:: STEP 1 -- Docker Desktop
echo [1/7] Checking Docker Desktop...
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Desktop is not running or not installed.
    echo.
    echo  1. Install: https://www.docker.com/products/docker-desktop/
    echo  2. Start Docker Desktop and wait for the tray icon to show Running
    echo  3. Run this script again
    pause
    exit /b 1
)
echo OK -- Docker Desktop is running
echo.

:: STEP 2 -- Docker Compose v2
echo [2/7] Checking Docker Compose v2...
docker compose version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker Compose v2 not found. Update Docker Desktop.
    pause
    exit /b 1
)
echo OK -- Docker Compose v2 available
echo.

:: STEP 3 -- .env file
echo [3/7] Checking .env configuration...
if not exist ".env" (
    if not exist ".env.example" (
        echo ERROR: .env.example not found. Run from the project root folder.
        pause
        exit /b 1
    )
    copy ".env.example" ".env" >nul
    echo CREATED .env from .env.example
    echo.
    echo ACTION REQUIRED -- Open .env and set:
    echo   DB_HOST / DB_PORT / DB_NAME / DB_USERNAME / DB_PASSWORD
    echo   PGVECTOR_HOST / PGVECTOR_PORT / PGVECTOR_DB / PGVECTOR_USER / PGVECTOR_PASSWORD
    echo   LLM_TOKEN_URL / LLM_GENERATE_URL / LLM_USERNAME / LLM_PASSWORD
    echo   OWUI_ADMIN_EMAIL / OWUI_ADMIN_PASSWORD
    echo   OWUI_SECRET_KEY / OWUI_AUTO_LOGIN_SECRET
    echo.
    set /p OPEN_NOW="Open .env in Notepad now? (Y/N): "
    if /i "!OPEN_NOW!"=="Y" (
        notepad .env
        echo.
        echo After saving .env press any key to continue...
        pause >nul
    ) else (
        echo Edit .env then re-run this script.
        pause
        exit /b 0
    )
) else (
    echo OK -- .env file exists
)

:: Parse key values from .env
set DB_HOST_VAL=
set DB_PORT_VAL=1433
set PG_HOST_VAL=
set PG_PORT_VAL=5432
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="DB_HOST"       set DB_HOST_VAL=%%B
    if /i "%%A"=="DB_PORT"       set DB_PORT_VAL=%%B
    if /i "%%A"=="PGVECTOR_HOST" set PG_HOST_VAL=%%B
    if /i "%%A"=="PGVECTOR_PORT" set PG_PORT_VAL=%%B
)
echo.

:: STEP 4 -- External DB connectivity
echo [4/7] Checking external database connectivity...
echo.

if "!DB_HOST_VAL!"=="" (
    echo SKIP -- DB_HOST not set in .env
) else (
    echo   SQL Server  : !DB_HOST_VAL!:!DB_PORT_VAL!
    powershell -NoProfile -Command ^
      "try{$t=New-Object Net.Sockets.TcpClient;$ar=$t.BeginConnect('!DB_HOST_VAL!',!DB_PORT_VAL!,$null,$null);$ok=$ar.AsyncWaitHandle.WaitOne(3000,$false);if($ok -and $t.Connected){$t.Close();exit 0}$t.Close();exit 1}catch{exit 1}" >nul 2>&1
    if errorlevel 1 (
        echo   FAIL -- Cannot reach SQL Server at !DB_HOST_VAL!:!DB_PORT_VAL!
        echo.
        echo   Check:
        echo     - DB_HOST and DB_PORT in .env are correct
        echo     - SQL Server TCP/IP is enabled ^(SQL Server Configuration Manager^)
        echo     - Firewall allows port !DB_PORT_VAL! from this machine
        echo     - Run SETUP-DB.bat to create the EnterpriseRAG database
        echo.
        set /p CONT_NOSQL="Continue anyway? (Y/N): "
        if /i not "!CONT_NOSQL!"=="Y" ( pause & exit /b 1 )
    ) else (
        echo   OK  -- SQL Server reachable
    )
)

if "!PG_HOST_VAL!"=="" (
    echo SKIP -- PGVECTOR_HOST not set in .env
) else (
    echo   PostgreSQL  : !PG_HOST_VAL!:!PG_PORT_VAL!
    powershell -NoProfile -Command ^
      "try{$t=New-Object Net.Sockets.TcpClient;$ar=$t.BeginConnect('!PG_HOST_VAL!',!PG_PORT_VAL!,$null,$null);$ok=$ar.AsyncWaitHandle.WaitOne(3000,$false);if($ok -and $t.Connected){$t.Close();exit 0}$t.Close();exit 1}catch{exit 1}" >nul 2>&1
    if errorlevel 1 (
        echo   FAIL -- Cannot reach PostgreSQL at !PG_HOST_VAL!:!PG_PORT_VAL!
        echo.
        echo   Check:
        echo     - PGVECTOR_HOST and PGVECTOR_PORT in .env are correct
        echo     - PostgreSQL is running and listening on that address
        echo     - pg_hba.conf allows connections from this machine
        echo     - Run SETUP-DB.bat to create the rag_vectors database
        echo.
        set /p CONT_NOPG="Continue anyway? (Y/N): "
        if /i not "!CONT_NOPG!"=="Y" ( pause & exit /b 1 )
    ) else (
        echo   OK  -- PostgreSQL reachable
    )
)
echo.

:: STEP 5 -- Auth mode
echo [5/7] Checking authentication mode...
set DEV_MODE_VAL=false
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="APP_DEV_MODE" set DEV_MODE_VAL=%%B
)
if /i "!DEV_MODE_VAL!"=="true" (
    echo DEV MODE ON -- JWT verification disabled. OK for local testing only.
) else (
    if not exist "backend\conf\jwt_public_key.pem" (
        echo WARNING: JWT public key not found at backend\conf\jwt_public_key.pem
        echo For dev testing set APP_DEV_MODE=true in .env
        set /p CONT_JWT="Continue anyway? (Y/N): "
        if /i not "!CONT_JWT!"=="Y" ( pause & exit /b 0 )
    ) else (
        echo OK -- JWT public key found
    )
)
echo.

:: STEP 6 -- Embedding model
echo [6/7] Checking embedding model...

docker run --rm -v :/models alpine ^
    sh -c "test -f /models/config.json && echo FOUND || echo EMPTY" > "%TEMP%\rag_model.txt" 2>nul

set MODEL_STATUS=EMPTY
for /f "delims=" %%i in ("%TEMP%\rag_model.txt") do set MODEL_STATUS=%%i
del "%TEMP%\rag_model.txt" 2>nul

if "!MODEL_STATUS!"=="FOUND" (
    echo OK -- Model already loaded in Docker volume
) else (
    echo WARNING: Model not found in Docker volume.
    echo.
    set /p MODEL_INPUT="Enter FULL path to model directory (or Enter to skip): "
    set "MODEL_INPUT=!MODEL_INPUT:"=!"
    if not "!MODEL_INPUT!"=="" (
        if exist "!MODEL_INPUT!\\" (
            echo Loading model via docker cp + move... (may take 1-5 minutes^)
            for %%F in ("!MODEL_INPUT!") do set "MODEL_FOLDER_NAME=%%~nxF"
            echo   Folder name: !MODEL_FOLDER_NAME!
            docker container rm rag_model_loader 2>nul
            docker run -d --name rag_model_loader -v :/models alpine tail -f /dev/null >nul 2>&1
            docker cp "!MODEL_INPUT!" rag_model_loader:/models/
            docker exec rag_model_loader sh -c "mv /models/!MODEL_FOLDER_NAME!/* /models/ && rmdir /models/!MODEL_FOLDER_NAME! || cp -r /models/!MODEL_FOLDER_NAME!/. /models/ && rm -rf /models/!MODEL_FOLDER_NAME!" >nul 2>&1
            docker exec rag_model_loader sh -c "test -f /models/config.json && echo VERIFIED || echo MISSING" > "%TEMP%\rag_v2.txt" 2>nul
            docker container rm -f rag_model_loader >nul 2>&1
            set V2=MISSING
            for /f "delims=" %%i in ("%TEMP%\rag_v2.txt") do set V2=%%i
            del "%TEMP%\rag_v2.txt" 2>nul
            if "!V2!"=="VERIFIED" (
                echo OK -- Model loaded and verified successfully
            ) else (
                echo ERROR: Model copy could not be verified.
                echo Run: scripts\load-model.bat "!MODEL_INPUT!"
            )
        ) else (
            echo ERROR: Path not found: !MODEL_INPUT!
            echo Run: scripts\load-model.bat "C:\path\to\model"
        )
    ) else (
        echo Skipped. Model-server will fail until model is loaded.
        echo Run: scripts\load-model.bat "C:\path\to\model"
    )
)
echo.

:: STEP 7 -- Build and start
echo [7/7] Building and starting Docker services...
echo   Services: model-server, backend, open-webui, owui-auth-proxy, nginx
echo.

if "%SKIP_BUILD%"=="0" (
    echo Building images (first build: 5-15 min, subsequent: ~1-2 min^)...
    docker compose build
    if errorlevel 1 (
        echo ERROR: Build failed. Check output above.
        pause
        exit /b 1
    )
    echo Build complete.
    echo.
)

docker compose up -d
if errorlevel 1 (
    echo ERROR: Failed to start services.
    pause
    exit /b 1
)

:: Wait for backend health
echo.
echo Waiting for services to become healthy...
echo (Model-server takes up to 90 s on first boot^)
echo.

set MAX_WAIT=150
set WAITED=0
set HEALTHY=0
:WAIT_LOOP
timeout /t 5 /nobreak >nul
set /a WAITED+=5
curl -s -o nul -w "%%{http_code}" http://localhost/api/v1/health/live 2>nul | findstr "200" >nul 2>&1
if not errorlevel 1 ( set HEALTHY=1 & goto WAIT_DONE )
if !WAITED! geq !MAX_WAIT! goto WAIT_DONE
echo Still waiting... !WAITED!s / !MAX_WAIT!s
goto WAIT_LOOP
:WAIT_DONE

echo.
docker compose ps
echo.

if "!HEALTHY!"=="1" (
    echo ==============================================================
    echo    Enterprise RAG v2.0.8 is READY!
    echo ==============================================================
) else (
    echo ==============================================================
    echo    Services started -- some may still be initialising.
    echo    Run: docker compose logs -f
    echo ==============================================================
)

echo.
echo Access Points:
echo   Auto-login  : http://localhost/?token=^<your_jwt^>
echo   Open WebUI  : http://localhost/
echo   API Docs    : http://localhost/api/docs
echo   Health      : http://localhost/api/v1/health
echo.
echo Useful Commands:
echo   All logs    : docker compose logs -f
echo   Backend log : docker compose logs -f backend
echo   Stop        : scripts\stop.bat
echo.

set /p OPEN_BROWSER="Open http://localhost/api/docs in browser? (Y/N): "
if /i "!OPEN_BROWSER!"=="Y" start http://localhost/api/docs

echo.
pause
endlocal
