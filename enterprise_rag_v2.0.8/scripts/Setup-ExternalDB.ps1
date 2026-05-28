#Requires -Version 5.1
<#
.SYNOPSIS
    Enterprise RAG v2.0.8 — External Database Setup (PowerShell)

.DESCRIPTION
    Applies the SQL Server and PostgreSQL/pgvector schemas to your external
    database instances. Run this ONCE before starting the application for
    the first time.

    Requires sqlcmd and/or psql to be installed and on PATH.
    If not available, instructions are printed to apply schemas manually
    using SSMS, Azure Data Studio, or pgAdmin.

.EXAMPLE
    .\scripts\Setup-ExternalDB.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Write-Header { param($m) Write-Host "`n$m" -ForegroundColor Cyan }
function Write-OK     { param($m) Write-Host "  OK    $m" -ForegroundColor Green }
function Write-Warn   { param($m) Write-Host "  WARN  $m" -ForegroundColor Yellow }
function Write-Err    { param($m) Write-Host "  ERR   $m" -ForegroundColor Red }
function Write-Info   { param($m) Write-Host "        $m" }

function Get-EnvValue {
    param([string]$Key, [string]$Default = "")
    $line = Get-Content ".env" -ErrorAction SilentlyContinue |
            Where-Object { $_ -match "^\s*$Key\s*=" -and $_ -notmatch "^\s*#" } |
            Select-Object -First 1
    if ($line) { return ($line -split "=", 2)[1].Trim() }
    return $Default
}

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "  Enterprise RAG v2.0.8 — External Database Setup" -ForegroundColor White
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".env")) {
    Write-Err ".env file not found. Run START.bat first to create it."
    Read-Host "`nPress Enter to exit"
    exit 1
}

# Read values from .env
$DbHost  = Get-EnvValue "DB_HOST"
$DbPort  = Get-EnvValue "DB_PORT"  "1433"
$DbName  = Get-EnvValue "DB_NAME"  "EnterpriseRAG"
$DbUser  = Get-EnvValue "DB_USERNAME"
$DbPass  = Get-EnvValue "DB_PASSWORD"
$PgHost  = Get-EnvValue "PGVECTOR_HOST"
$PgPort  = Get-EnvValue "PGVECTOR_PORT" "5432"
$PgDb    = Get-EnvValue "PGVECTOR_DB"   "rag_vectors"
$PgUser  = Get-EnvValue "PGVECTOR_USER"
$PgPass  = Get-EnvValue "PGVECTOR_PASSWORD"

Write-Host "  SQL Server : ${DbHost}:${DbPort}   db=$DbName" -ForegroundColor White
Write-Host "  PostgreSQL : ${PgHost}:${PgPort}   db=$PgDb"   -ForegroundColor White
Write-Host ""

$confirm = Read-Host "Apply schemas to these servers? (Y/n)"
if ($confirm -match "^[Nn]") { Write-Host "Cancelled."; exit 0 }

# =============================================================================
# SQL Server
# =============================================================================
Write-Header "--- SQL Server ---"

$sqlcmdFound = $null -ne (Get-Command sqlcmd -ErrorAction SilentlyContinue)

if (-not $sqlcmdFound) {
    Write-Warn "sqlcmd not found in PATH."
    Write-Info ""
    Write-Info "Apply sql\01_schema.sql manually:"
    Write-Info "  Tool     : SSMS, Azure Data Studio, or DBeaver"
    Write-Info "  Server   : ${DbHost},${DbPort}"
    Write-Info "  Database : $DbName  (create it first if it does not exist)"
    Write-Info "  Script   : $ProjectRoot\sql\01_schema.sql"
    Write-Info ""
    Write-Info "To install sqlcmd:"
    Write-Info "  https://learn.microsoft.com/en-us/sql/tools/sqlcmd/sqlcmd-utility"
} else {
    Write-Info "Creating database [$DbName] if it does not exist..."
    try {
        sqlcmd -S "${DbHost},${DbPort}" -U $DbUser -P $DbPass `
            -Q "IF NOT EXISTS (SELECT name FROM sys.databases WHERE name='$DbName') CREATE DATABASE [$DbName];" `
            -b 2>&1 | ForEach-Object { if ($_) { Write-Info $_ } }

        Write-Info "Applying schema from sql\01_schema.sql..."
        sqlcmd -S "${DbHost},${DbPort}" -U $DbUser -P $DbPass `
            -d $DbName -i "sql\01_schema.sql" -b 2>&1 |
            ForEach-Object { if ($_) { Write-Info $_ } }

        Write-OK "SQL Server schema applied successfully."
    } catch {
        Write-Err "SQL Server setup failed: $_"
        Write-Info "Apply sql\01_schema.sql manually using SSMS or Azure Data Studio."
    }
}

# =============================================================================
# PostgreSQL + pgvector
# =============================================================================
Write-Header "--- PostgreSQL + pgvector ---"

$psqlFound = $null -ne (Get-Command psql -ErrorAction SilentlyContinue)

if (-not $psqlFound) {
    Write-Warn "psql not found in PATH."
    Write-Info ""
    Write-Info "Apply sql\02_pgvector_schema.sql manually:"
    Write-Info "  Tool     : pgAdmin, DBeaver, or psql"
    Write-Info "  Server   : ${PgHost}:${PgPort}"
    Write-Info "  Database : $PgDb  (create it first if it does not exist)"
    Write-Info "  Script   : $ProjectRoot\sql\02_pgvector_schema.sql"
    Write-Info ""
    Write-Info "Manual psql commands (if psql is available elsewhere):"
    Write-Info "  `$env:PGPASSWORD='$PgPass'"
    Write-Info "  psql -h $PgHost -p $PgPort -U $PgUser -c `"CREATE DATABASE $PgDb;`""
    Write-Info "  psql -h $PgHost -p $PgPort -U $PgUser -d $PgDb -f sql\02_pgvector_schema.sql"
    Write-Info ""
    Write-Info "Download PostgreSQL client tools:"
    Write-Info "  https://www.postgresql.org/download/windows/"
} else {
    $env:PGPASSWORD = $PgPass

    Write-Info "Creating database [$PgDb] if it does not exist..."
    try {
        $dbExists = psql -h $PgHost -p $PgPort -U $PgUser -d postgres `
            -tAc "SELECT 1 FROM pg_database WHERE datname='$PgDb'" 2>$null
        if ($dbExists -ne "1") {
            psql -h $PgHost -p $PgPort -U $PgUser -d postgres `
                -c "CREATE DATABASE $PgDb;" 2>&1 | ForEach-Object { if ($_) { Write-Info $_ } }
        } else {
            Write-Info "Database [$PgDb] already exists."
        }

        Write-Info "Installing pgvector extension..."
        psql -h $PgHost -p $PgPort -U $PgUser -d $PgDb `
            -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>&1 |
            ForEach-Object { if ($_) { Write-Info $_ } }

        Write-Info "Applying schema from sql\02_pgvector_schema.sql..."
        psql -h $PgHost -p $PgPort -U $PgUser -d $PgDb `
            -f "sql\02_pgvector_schema.sql" 2>&1 |
            ForEach-Object { if ($_) { Write-Info $_ } }

        Write-OK "PostgreSQL schema applied successfully."
    } catch {
        Write-Err "PostgreSQL setup failed: $_"
        Write-Info "Apply sql\02_pgvector_schema.sql manually using pgAdmin."
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

# =============================================================================
# Summary
# =============================================================================
Write-Host ""
Write-Host "=============================================================" -ForegroundColor Green
Write-Host "  Database setup complete!" -ForegroundColor Green
Write-Host "  Next: double-click START.bat to launch the application." -ForegroundColor Green
Write-Host "=============================================================" -ForegroundColor Green
Write-Host ""
Read-Host "Press Enter to exit"
