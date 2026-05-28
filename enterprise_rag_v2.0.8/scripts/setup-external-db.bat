@echo off
:: =============================================================================
:: Enterprise RAG v2.0.8 -- External Database Setup (Windows CMD)
:: Applies SQL Server and PostgreSQL schemas to your external instances.
:: Run this ONCE before starting the app for the first time.
::
:: Requirements (optional -- shows manual instructions if not found):
::   sqlcmd : https://learn.microsoft.com/en-us/sql/tools/sqlcmd/sqlcmd-utility
::   psql   : https://www.postgresql.org/download/windows/
::
:: If these tools are not available, open SSMS or pgAdmin and run:
::   SQL Server : sql\01_schema.sql  (database: EnterpriseRAG)
::   PostgreSQL : sql\02_pgvector_schema.sql  (database: rag_vectors)
::
:: Usage: scripts\setup-external-db.bat
:: =============================================================================
setlocal EnableDelayedExpansion
title Enterprise RAG -- External Database Setup

cd /d "%~dp0.."

echo.
echo ==============================================================
echo    Enterprise RAG v2.0.8 -- External Database Setup
echo ==============================================================
echo.

if not exist ".env" (
    echo ERROR: .env not found. Run START.bat first to create it.
    pause
    exit /b 1
)

:: Parse .env values
set DB_HOST_VAL=
set DB_PORT_VAL=1433
set DB_NAME_VAL=EnterpriseRAG
set DB_USER_VAL=
set DB_PASS_VAL=
set PG_HOST_VAL=
set PG_PORT_VAL=5432
set PG_DB_VAL=rag_vectors
set PG_USER_VAL=
set PG_PASS_VAL=

for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="DB_HOST"           set DB_HOST_VAL=%%B
    if /i "%%A"=="DB_PORT"           set DB_PORT_VAL=%%B
    if /i "%%A"=="DB_NAME"           set DB_NAME_VAL=%%B
    if /i "%%A"=="DB_USERNAME"       set DB_USER_VAL=%%B
    if /i "%%A"=="DB_PASSWORD"       set DB_PASS_VAL=%%B
    if /i "%%A"=="PGVECTOR_HOST"     set PG_HOST_VAL=%%B
    if /i "%%A"=="PGVECTOR_PORT"     set PG_PORT_VAL=%%B
    if /i "%%A"=="PGVECTOR_DB"       set PG_DB_VAL=%%B
    if /i "%%A"=="PGVECTOR_USER"     set PG_USER_VAL=%%B
    if /i "%%A"=="PGVECTOR_PASSWORD" set PG_PASS_VAL=%%B
)

echo   SQL Server : !DB_HOST_VAL!:!DB_PORT_VAL!  db=!DB_NAME_VAL!
echo   PostgreSQL : !PG_HOST_VAL!:!PG_PORT_VAL!  db=!PG_DB_VAL!
echo.
set /p CONFIRM="Apply schemas to these servers? (Y/N): "
if /i not "!CONFIRM!"=="Y" (
    echo Cancelled.
    pause
    exit /b 0
)

:: =============================================================================
:: SQL Server
:: =============================================================================
echo.
echo --- SQL Server ---

where sqlcmd >nul 2>&1
if errorlevel 1 (
    echo sqlcmd not found in PATH.
    echo.
    echo Apply sql\01_schema.sql manually:
    echo   Tool    : SSMS or Azure Data Studio
    echo   Server  : !DB_HOST_VAL!,!DB_PORT_VAL!
    echo   Database: !DB_NAME_VAL!  ^(create it first if needed^)
    echo   Script  : %CD%\sql\01_schema.sql
    echo.
    echo Install sqlcmd:
    echo   https://learn.microsoft.com/en-us/sql/tools/sqlcmd/sqlcmd-utility
) else (
    echo Creating database [!DB_NAME_VAL!] if it does not exist...
    sqlcmd -S "!DB_HOST_VAL!,!DB_PORT_VAL!" -U "!DB_USER_VAL!" -P "!DB_PASS_VAL!" ^
        -Q "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='!DB_NAME_VAL!') CREATE DATABASE [!DB_NAME_VAL!];" ^
        -b
    if errorlevel 1 (
        echo FAILED to create database. Check credentials and connectivity.
        goto PG_SECTION
    )

    echo Applying schema from sql\01_schema.sql...
    sqlcmd -S "!DB_HOST_VAL!,!DB_PORT_VAL!" -U "!DB_USER_VAL!" -P "!DB_PASS_VAL!" ^
        -d "!DB_NAME_VAL!" -i "sql\01_schema.sql" -b
    if errorlevel 1 (
        echo FAILED: Check sql\01_schema.sql and the error above.
    ) else (
        echo OK -- SQL Server schema applied successfully.
    )
)

:: =============================================================================
:: PostgreSQL + pgvector
:: =============================================================================
:PG_SECTION
echo.
echo --- PostgreSQL + pgvector ---

where psql >nul 2>&1
if errorlevel 1 (
    echo psql not found in PATH.
    echo.
    echo Apply sql\02_pgvector_schema.sql manually:
    echo   Tool    : pgAdmin or DBeaver
    echo   Server  : !PG_HOST_VAL!:!PG_PORT_VAL!
    echo   Database: !PG_DB_VAL!  ^(create it first if needed^)
    echo   Script  : %CD%\sql\02_pgvector_schema.sql
    echo.
    echo Or if psql is available elsewhere, run:
    echo   set PGPASSWORD=!PG_PASS_VAL!
    echo   psql -h !PG_HOST_VAL! -p !PG_PORT_VAL! -U !PG_USER_VAL! -c "CREATE DATABASE !PG_DB_VAL!;"
    echo   psql -h !PG_HOST_VAL! -p !PG_PORT_VAL! -U !PG_USER_VAL! -d !PG_DB_VAL! -f sql\02_pgvector_schema.sql
    echo.
    echo Download PostgreSQL client tools:
    echo   https://www.postgresql.org/download/windows/
) else (
    set PGPASSWORD=!PG_PASS_VAL!

    echo Creating database [!PG_DB_VAL!] if it does not exist...
    psql -h "!PG_HOST_VAL!" -p "!PG_PORT_VAL!" -U "!PG_USER_VAL!" -d postgres ^
        -c "SELECT 1 FROM pg_database WHERE datname='!PG_DB_VAL!'" 2>nul | findstr /c:"1 row" >nul
    if errorlevel 1 (
        psql -h "!PG_HOST_VAL!" -p "!PG_PORT_VAL!" -U "!PG_USER_VAL!" -d postgres ^
            -c "CREATE DATABASE !PG_DB_VAL!;"
    ) else (
        echo Database [!PG_DB_VAL!] already exists.
    )

    echo Installing pgvector extension...
    psql -h "!PG_HOST_VAL!" -p "!PG_PORT_VAL!" -U "!PG_USER_VAL!" -d "!PG_DB_VAL!" ^
        -c "CREATE EXTENSION IF NOT EXISTS vector;"

    echo Applying schema from sql\02_pgvector_schema.sql...
    psql -h "!PG_HOST_VAL!" -p "!PG_PORT_VAL!" -U "!PG_USER_VAL!" -d "!PG_DB_VAL!" ^
        -f "sql\02_pgvector_schema.sql"
    if errorlevel 1 (
        echo FAILED: Check sql\02_pgvector_schema.sql and the error above.
    ) else (
        echo OK -- PostgreSQL schema applied successfully.
    )

    set PGPASSWORD=
)

echo.
echo ==============================================================
echo    Database setup complete!
echo    Run START.bat to launch the application.
echo ==============================================================
echo.
pause
endlocal
